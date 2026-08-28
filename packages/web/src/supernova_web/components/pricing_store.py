"""模型定价两层运行时存储：全局价目表 + 工作区覆盖（spec 2026-08-28 §4.2）。

优先级（与 core pricing.py `_pricing` 三层合并一致，外加 builtin 底座）：
    builtin < profile_env（web 进程 SUPERNOVA_PRICING_OVERRIDE）< global < workspace

落盘：
- 全局表 ``<workspaces_dir>/pricing.json``——admin 在设置页保存的**完整生效表快照**，
  存在即接管 profile env 层（「界面为准」语义）。core 侧经 env 键
  ``SUPERNOVA_GLOBAL_PRICING``（app 挂载时 setdefault 指向本文件）读取。
- 工作区覆盖 ``<workspaces_dir>/<ws>/pricing.override.json``——SSOT = 文件存在性，
  **不写 ws config env 段**（env 文本框契约「文本=完整定义」，程序写键会被下次
  保存静默清除）；由 scan_manager._resolve_env_overrides 注入压过手写键。

模型 key 落盘前经 normalize_model 归一：core 的 table.update 直接吃层文件 key，
归一 id 是 compute_cost 的查询形态，存原始形态（如 GLM-5.2[1m]）会 miss 记 0 成本。

模型级币种（2026-08-28）：价格对象内可选 ``currency`` 键（仅 CNY/USD）覆盖表级默认；
resolve_effective 行输出的 ``currency`` 是**兄弟字段**（null=跟随表级，不 resolve 成
具体值——保住「跟随」语义，避免 ws 覆盖快照把每行写成显式币种）；写路径 strip
null/垃圾。整对象替换时高层缺键 → 低层模型级币种丢失（与 core 同语义）。

原子写（tmp → replace）照 branding_store 范本；损坏文件不当机（层视为空 + corrupt 标志）。
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from pathlib import Path

from supernova_core.agents.pricing import BUILTIN_PRICING_CNY, normalize_model

_log = logging.getLogger(__name__)

GLOBAL_FILENAME = "pricing.json"
WS_OVERRIDE_FILENAME = "pricing.override.json"

_CURRENCIES = ("CNY", "USD")
_TIER_KEYS = ("input", "output", "cache_read", "cache_creation")


def _sanitized_model(prices: dict) -> dict:
    """价格对象 → 落盘形态：4 档 + 仅当模型级 currency 合法时附带（None/垃圾不落键）。

    与 core `_model_currency` 同判（合法币种白名单）；None = 跟随表级默认。
    """
    out = {k: prices[k] for k in _TIER_KEYS}
    cur = prices.get("currency")
    if cur in _CURRENCIES:
        out["currency"] = cur
    return out


def _read_layer_file(path: Path) -> tuple[dict | None, bool]:
    """读一层定价文件 → (payload, corrupt)。

    (None, False)=文件不存在（层缺席）；({}, True)=损坏（JSON 坏 / 顶层非 dict /
    读失败）；(dict, False)=正常。损坏不当机：调用方按空层处理并透出 corrupt。
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None, False
    except (json.JSONDecodeError, OSError) as e:
        _log.warning("定价文件损坏（%s：%s），按空层处理", path, e)
        return {}, True
    if not isinstance(data, dict):
        _log.warning("定价文件顶层非 object（%s），按空层处理", path)
        return {}, True
    return data, False


def _layer_models(layer: dict) -> tuple[dict, str | None]:
    """层 payload → (归一化 models, currency)。

    新 schema {"currency","models"} → currency 取层值（缺省 CNY）；
    旧 flat schema {model: prices}（仅 profile_env 现实存在）→ currency 视作 CNY。
    空层 / models 非 dict → ({}, None)（不影响 currency）。
    """
    if isinstance(layer.get("models"), dict):
        cur = layer.get("currency", "CNY")
        if not isinstance(cur, str) or not cur:
            cur = "CNY"
        return layer["models"], cur
    if layer:
        return layer, "CNY"  # 旧 flat：币种回落 CNY（core 现状语义）
    return {}, None


class PricingStore:
    def __init__(self, workspaces_dir: Path) -> None:
        self._dir = Path(workspaces_dir)

    # ---- 路径 ----

    def _global_path(self) -> Path:
        return self._dir / GLOBAL_FILENAME

    def _ws_path(self, ws: str) -> Path:
        return self._dir / ws / WS_OVERRIDE_FILENAME

    # ---- 原子写 ----

    @staticmethod
    def _atomic_write(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".pricing.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- 全局表 ----

    def read_global_ex(self) -> tuple[dict | None, bool]:
        """(payload, corrupt)；payload=None 表示未创建（未接管）。"""
        return _read_layer_file(self._global_path())

    def read_global(self) -> dict | None:
        payload, _corrupt = self.read_global_ex()
        return payload

    def write_global(self, currency: str, models: dict) -> None:
        """校验 + key 归一 + 原子写完整快照。"""
        self.validate(currency, models)
        self._atomic_write(self._global_path(), {
            "currency": currency,
            "models": {normalize_model(k): _sanitized_model(v) for k, v in models.items()},
        })

    def clear_global(self) -> None:
        """删全局表（不存在幂等）。"""
        try:
            self._global_path().unlink()
        except FileNotFoundError:
            pass

    # ---- 工作区覆盖 ----

    def read_ws_override_ex(self, ws: str) -> tuple[dict | None, bool]:
        return _read_layer_file(self._ws_path(ws))

    def read_ws_override(self, ws: str) -> dict | None:
        payload, _corrupt = self.read_ws_override_ex(ws)
        return payload

    def write_ws_override(self, ws: str, currency: str, models: dict) -> None:
        self.validate(currency, models)
        self._atomic_write(self._ws_path(ws), {
            "currency": currency,
            "models": {normalize_model(k): _sanitized_model(v) for k, v in models.items()},
        })

    def clear_ws_override(self, ws: str) -> None:
        """删覆盖文件（不存在幂等）——恢复继承全局。"""
        try:
            self._ws_path(ws).unlink()
        except FileNotFoundError:
            pass

    # ---- 合并 ----

    def resolve_effective(self, ws: str | None = None) -> dict:
        """按 builtin < profile_env < global < workspace 合并生效表。

        返回 {"currency", "models": [{model, prices{4档}, source}]}；models 含全部
        层出现过的模型（并集），各标最高优先来源；ws=None 为全局视角（不含 ws 层）。
        """
        layers: list[tuple[str, dict | None]] = [
            ("profile_env", self._load_profile_env_layer()),
            ("global", self.read_global()),
        ]
        if ws is not None:
            layers.append(("workspace", self.read_ws_override(ws)))

        table: dict[str, dict] = {}
        source_of: dict[str, str] = {}
        currency_of: dict[str, str | None] = {}  # 模型级币种原始值（null=跟随表级）
        for model, prices in BUILTIN_PRICING_CNY.items():
            table[model] = dict(prices)
            source_of[model] = "builtin"

        currency = "CNY"  # builtin 底座币种
        for name, layer in layers:  # 低 → 高，后者覆盖前者
            if not layer:
                continue
            models, cur = _layer_models(layer)
            if not models:
                continue
            if cur is not None:
                currency = cur
            for raw_key, prices in models.items():
                key = normalize_model(raw_key)
                if not key or not isinstance(prices, dict):
                    continue  # 手写文件的脏行：跳过该模型，不让整层失效
                four = {k: float(prices.get(k, 0) or 0) for k in _TIER_KEYS}
                table[key] = four
                source_of[key] = name
                row_cur = prices.get("currency")
                currency_of[key] = row_cur if row_cur in _CURRENCIES else None

        models_out = [
            {"model": m, "prices": table[m], "source": source_of[m], "currency": currency_of.get(m)}
            for m in sorted(table)
        ]
        return {"currency": currency, "models": models_out}

    def _load_profile_env_layer(self) -> dict | None:
        path = os.environ.get("SUPERNOVA_PRICING_OVERRIDE")
        if not path:
            return None
        payload, _corrupt = _read_layer_file(Path(path))
        return payload

    # ---- 校验 ----

    @staticmethod
    def validate(currency: str, models: dict) -> None:
        """落盘前校验；非法抛 ValueError（API 层转 400）。

        - currency ∈ {CNY, USD}
        - models 非空 dict（清除走 clear_*，不走空表写入）
        - 模型 key 归一后非空且彼此不重复（glm-5.2 与 GLM-5.2[1m] 冲突）
        - 4 档价格齐全、为有限数（拒 bool）且 ≥ 0
        - 模型级 currency（可选）∈ {CNY, USD}；None/缺省 = 跟随表级
        """
        if currency not in _CURRENCIES:
            raise ValueError(f"currency 必须是 {'/'.join(_CURRENCIES)}，收到 {currency!r}")
        if not isinstance(models, dict) or not models:
            raise ValueError("models 必须是非空对象（清除定价请用删除操作）")
        seen: set[str] = set()
        for key, prices in models.items():
            normalized = normalize_model(key) if isinstance(key, str) else ""
            if not normalized:
                raise ValueError(f"模型名 {key!r} 无效（归一后为空）")
            if normalized in seen:
                raise ValueError(f"模型名归一后冲突：{key!r} → {normalized}")
            seen.add(normalized)
            if not isinstance(prices, dict):
                raise ValueError(f"模型 {key!r} 的价格必须是对象")
            model_cur = prices.get("currency", None)
            if model_cur is not None and model_cur not in _CURRENCIES:
                raise ValueError(
                    f"模型 {key!r} 的 currency 必须是 {'/'.join(_CURRENCIES)}，收到 {model_cur!r}")
            for tier in _TIER_KEYS:
                v = prices.get(tier)
                if v is None:
                    raise ValueError(f"模型 {key!r} 缺 {tier} 档价格（须提交全部 4 档）")
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise ValueError(f"模型 {key!r} 的 {tier} 必须是数字，收到 {v!r}")
                if not math.isfinite(v) or v < 0:
                    raise ValueError(f"模型 {key!r} 的 {tier} 必须是 ≥ 0 的有限数，收到 {v!r}")
