"""PricingStore：全局价目表 + 工作区覆盖的落盘 / 校验 / resolve_effective 来源标注。

spec: docs/superpowers/specs/2026-08-28-global-pricing-console-design.md §4.2
优先级：builtin < profile_env < global < workspace；币种 = 最高优先非空层。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from supernova_web.components.pricing_store import PricingStore


@pytest.fixture(autouse=True)
def _clean_pricing_env(monkeypatch):
    """隔离宿主 env：profile_env 层默认不存在（部分开发机 shell 设了该键）。"""
    monkeypatch.delenv("SUPERNOVA_PRICING_OVERRIDE", raising=False)


@pytest.fixture
def store(tmp_path):
    return PricingStore(tmp_path)


def _tiers(i=1.0, o=2.0, cr=0.5, cc=0.0):
    return {"input": i, "output": o, "cache_read": cr, "cache_creation": cc}


# ---- 全局表读写 / 原子写 ----


def test_read_global_absent_returns_none(store):
    assert store.read_global() is None


def test_write_global_roundtrip_and_normalized_keys(store, tmp_path):
    store.write_global("CNY", {"GLM-5.2[1m]": _tiers(), "deepseek-v4-pro": _tiers(3, 6, 0.025)})
    # 落盘 key 归一（core pricing.py 直接 table.update 层文件，不归一文件 key——
    # store 必须落归一 id 才能让 compute_cost 命中）。
    data = json.loads((tmp_path / "pricing.json").read_text("utf-8"))
    assert set(data["models"]) == {"glm-5.2", "deepseek-v4-pro"}
    assert data["currency"] == "CNY"
    assert data["models"]["glm-5.2"]["input"] == 1.0
    assert store.read_global() == data


def test_write_global_atomic_no_tmp_leftover(store, tmp_path):
    store.write_global("USD", {"m": _tiers()})
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    assert (tmp_path / "pricing.json").exists()


def test_clear_global_idempotent(store):
    store.clear_global()  # 不存在 → 不抛
    store.write_global("CNY", {"m": _tiers()})
    store.clear_global()
    assert store.read_global() is None
    store.clear_global()  # 再删仍幂等


def test_read_global_corrupt_json_returns_empty_and_flag(store, tmp_path):
    (tmp_path / "pricing.json").write_text("{not json", encoding="utf-8")
    assert store.read_global() == {}
    assert store.read_global_ex() == ({}, True)


def test_read_global_non_dict_top_level_treated_corrupt(store, tmp_path):
    (tmp_path / "pricing.json").write_text("[1,2]", encoding="utf-8")
    assert store.read_global_ex() == ({}, True)


def test_read_global_ex_normal(store):
    assert store.read_global_ex() == (None, False)
    store.write_global("CNY", {"m": _tiers()})
    payload, corrupt = store.read_global_ex()
    assert corrupt is False and payload["currency"] == "CNY"


# ---- 工作区覆盖读写 ----


def test_ws_override_roundtrip_and_clear(store, tmp_path):
    assert store.read_ws_override("ws-a") is None
    store.write_ws_override("ws-a", "CNY", {"glm-5.2": _tiers(9, 30, 3)})
    path = tmp_path / "ws-a" / "pricing.override.json"
    assert path.exists()
    assert store.read_ws_override("ws-a")["models"]["glm-5.2"]["input"] == 9.0
    store.clear_ws_override("ws-a")
    assert not path.exists()
    store.clear_ws_override("ws-a")  # 幂等


def test_read_ws_override_corrupt_returns_empty(store, tmp_path):
    d = tmp_path / "ws-a"
    d.mkdir()
    (d / "pricing.override.json").write_text("garbage{", encoding="utf-8")
    assert store.read_ws_override("ws-a") == {}
    assert store.read_ws_override_ex("ws-a") == ({}, True)


# ---- validate 规则全集 ----


def test_validate_accepts_valid(store):
    PricingStore.validate("CNY", {"glm-5.2": _tiers(), "deepseek-v4-pro": _tiers()})
    PricingStore.validate("USD", {"m": _tiers(0, 0, 0, 0)})


@pytest.mark.parametrize("currency", ["cny", "RMB", "", "EUR", None])
def test_validate_rejects_bad_currency(store, currency):
    with pytest.raises(ValueError):
        PricingStore.validate(currency, {"m": _tiers()})


def test_validate_rejects_empty_models(store):
    with pytest.raises(ValueError):
        PricingStore.validate("CNY", {})


def test_validate_rejects_normalized_conflict(store):
    # normalize_model("glm-5.2[1m]") == normalize_model("GLM-5.2") == "glm-5.2"
    with pytest.raises(ValueError):
        PricingStore.validate("CNY", {"glm-5.2": _tiers(), "GLM-5.2[1m]": _tiers()})


def test_validate_rejects_empty_or_unnormalizable_model_key(store):
    with pytest.raises(ValueError):
        PricingStore.validate("CNY", {"  ": _tiers()})
    with pytest.raises(ValueError):
        PricingStore.validate("CNY", {"[1m]": _tiers()})  # 归一后为空
    with pytest.raises(ValueError):
        PricingStore.validate("CNY", {"": _tiers()})


@pytest.mark.parametrize("tiers", [
    {"input": -1, "output": 2, "cache_read": 0, "cache_creation": 0},   # 负价
    {"input": math.nan, "output": 2, "cache_read": 0, "cache_creation": 0},   # NaN
    {"input": math.inf, "output": 2, "cache_read": 0, "cache_creation": 0},   # inf
    {"input": "1", "output": 2, "cache_read": 0, "cache_creation": 0},        # 字符串数
    {"input": True, "output": 2, "cache_read": 0, "cache_creation": 0},       # bool（int 子类，明确拒）
    {"input": 1, "cache_read": 0, "cache_creation": 0},                       # 缺 output 档
])
def test_validate_rejects_bad_prices(store, tiers):
    with pytest.raises(ValueError):
        PricingStore.validate("CNY", {"m": tiers})


def test_validate_rejects_non_dict_models(store):
    with pytest.raises(ValueError):
        PricingStore.validate("CNY", ["m"])
    with pytest.raises(ValueError):
        PricingStore.validate("CNY", {"m": [1, 2, 3, 4]})


# ---- resolve_effective 四态来源标注 ----


def _write_pricing_file(path: Path, payload: dict) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_resolve_effective_builtin_only(store):
    eff = store.resolve_effective()
    assert eff["currency"] == "CNY"
    by_model = {m["model"]: m for m in eff["models"]}
    assert by_model["glm-5.2"]["source"] == "builtin"
    assert by_model["glm-5.2"]["prices"]["input"] == 8.0
    assert "glm-5.3" in by_model  # builtin 全量在列


def test_resolve_effective_profile_env_layer(store, tmp_path, monkeypatch):
    p = _write_pricing_file(tmp_path / "env_pricing.json", {
        "currency": "USD",
        "models": {"glm-5.2": _tiers(1, 2, 3, 4), "extra-model": _tiers()},
    })
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", p)
    eff = store.resolve_effective()
    by_model = {m["model"]: m for m in eff["models"]}
    assert by_model["glm-5.2"]["source"] == "profile_env"
    assert by_model["glm-5.2"]["prices"]["input"] == 1.0
    assert by_model["extra-model"]["source"] == "profile_env"   # 并集：builtin 没有的也在列
    assert by_model["glm-4.5-air"]["source"] == "builtin"       # 未覆盖的保持 builtin
    assert eff["currency"] == "USD"


def test_resolve_effective_profile_env_flat_schema_compat(store, tmp_path, monkeypatch):
    # 旧 flat schema（{model: prices}）→ 合并 + 币种 CNY（core 现状兼容语义）
    p = _write_pricing_file(tmp_path / "flat.json", {"glm-5.2": _tiers(5, 5, 5, 5)})
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", p)
    eff = store.resolve_effective()
    assert eff["currency"] == "CNY"
    by_model = {m["model"]: m for m in eff["models"]}
    assert by_model["glm-5.2"]["prices"]["input"] == 5.0


def test_resolve_effective_global_beats_profile_env(store, tmp_path, monkeypatch):
    p = _write_pricing_file(tmp_path / "env.json", {
        "currency": "USD", "models": {"glm-5.2": _tiers(1, 1, 1, 1)}})
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", p)
    store.write_global("CNY", {"glm-5.2": _tiers(2, 2, 2, 2)})
    eff = store.resolve_effective()
    by_model = {m["model"]: m for m in eff["models"]}
    assert by_model["glm-5.2"]["source"] == "global"        # 接管语义：global 压过 profile env
    assert by_model["glm-5.2"]["prices"]["input"] == 2.0
    assert eff["currency"] == "CNY"                          # 币种也取 global 层


def test_resolve_effective_workspace_beats_global(store, tmp_path):
    store.write_global("CNY", {"glm-5.2": _tiers(2, 2, 2, 2), "m2": _tiers()})
    store.write_ws_override("ws-a", "USD", {"glm-5.2": _tiers(3, 3, 3, 3)})
    eff = store.resolve_effective("ws-a")
    by_model = {m["model"]: m for m in eff["models"]}
    assert by_model["glm-5.2"]["source"] == "workspace"
    assert by_model["glm-5.2"]["prices"]["input"] == 3.0
    assert by_model["m2"]["source"] == "global"              # 未被 ws 覆盖的保持 global
    assert eff["currency"] == "USD"


def test_resolve_effective_ws_none_excludes_workspace_layer(store, tmp_path):
    store.write_ws_override("ws-a", "USD", {"glm-5.2": _tiers(9, 9, 9, 9)})
    eff = store.resolve_effective()   # ws=None：全局视角
    by_model = {m["model"]: m for m in eff["models"]}
    assert by_model["glm-5.2"]["source"] == "builtin"        # ws 覆盖不可见
    assert eff["currency"] == "CNY"


def test_resolve_effective_corrupt_layers_skipped(store, tmp_path):
    (tmp_path / "pricing.json").write_text("bad{", encoding="utf-8")
    d = tmp_path / "ws-a"
    d.mkdir()
    (d / "pricing.override.json").write_text("bad{", encoding="utf-8")
    eff = store.resolve_effective("ws-a")   # 两层损坏 → 不抛，全 builtin
    by_model = {m["model"]: m for m in eff["models"]}
    assert by_model["glm-5.2"]["source"] == "builtin"


def test_resolve_effective_prices_always_four_tiers(store, tmp_path, monkeypatch):
    # 层文件缺 cache_creation（手写旧文件）→ 输出补 0，展示层不用再防
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"currency": "CNY", "models": {"glm-5.2": {"input": 1, "output": 2, "cache_read": 3}}}), encoding="utf-8")
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", str(p))
    eff = store.resolve_effective()
    by_model = {m["model"]: m for m in eff["models"]}
    assert by_model["glm-5.2"]["prices"]["cache_creation"] == 0.0


# ---- 模型级币种（2026-08-28 per-model currency）----
# 语义：价格对象内可选 currency 键；行输出为 row 兄弟字段（不进 prices）；
# null = 跟随表级默认，不在 store 侧 resolve 成具体值（保住「跟随」语义，
# 否则 ws 覆盖快照保存会把每行都写成显式 currency）。


def test_write_global_keeps_model_currency_and_strips_null(store, tmp_path):
    store.write_global("CNY", {
        "m-usd": {**_tiers(), "currency": "USD"},
        "m-follow": {**_tiers(), "currency": None},
    })
    data = json.loads((tmp_path / "pricing.json").read_text("utf-8"))
    assert data["models"]["m-usd"]["currency"] == "USD"
    assert "currency" not in data["models"]["m-follow"]   # None 不落键（旧文件形态）


def test_write_ws_override_keeps_model_currency(store, tmp_path):
    store.write_ws_override("ws-a", "CNY", {"m": {**_tiers(), "currency": "USD"}})
    assert store.read_ws_override("ws-a")["models"]["m"]["currency"] == "USD"


def test_validate_accepts_model_level_currency(store):
    PricingStore.validate("CNY", {"m": {**_tiers(), "currency": "USD"}})
    PricingStore.validate("CNY", {"m": {**_tiers(), "currency": "CNY"}})
    PricingStore.validate("CNY", {"m": {**_tiers(), "currency": None}})   # = 缺省
    PricingStore.validate("CNY", {"m": _tiers()})                          # 无键


@pytest.mark.parametrize("cur", ["cny", "EUR", "", 123, True])
def test_validate_rejects_bad_model_currency(store, cur):
    with pytest.raises(ValueError):
        PricingStore.validate("CNY", {"m": {**_tiers(), "currency": cur}})


def test_resolve_effective_row_currency_model_level(store, tmp_path, monkeypatch):
    p = _write_pricing_file(tmp_path / "env.json", {
        "currency": "CNY",
        "models": {"m-usd": {**_tiers(), "currency": "USD"}, "m-follow": _tiers()},
    })
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", p)
    eff = store.resolve_effective()
    by_model = {m["model"]: m for m in eff["models"]}
    assert by_model["m-usd"]["currency"] == "USD"     # 兄弟字段透出原始值
    assert by_model["m-follow"]["currency"] is None   # 未指定 → null（不 resolve 成表级）
    assert by_model["glm-5.2"]["currency"] is None    # builtin 行同样 null
    assert set(by_model["m-usd"]["prices"]) == {"input", "output", "cache_read", "cache_creation"}  # 不进 prices


def test_resolve_effective_row_currency_object_replace(store, tmp_path):
    # global 层 m 带 USD；ws 层整对象重定义 m（无 currency）→ 模型级丢失（回落表级，core 同语义）
    store.write_global("CNY", {"m": {**_tiers(), "currency": "USD"}})
    store.write_ws_override("ws-a", "CNY", {"m": _tiers(9, 9, 9, 9)})
    eff = store.resolve_effective("ws-a")
    by_model = {m["model"]: m for m in eff["models"]}
    assert by_model["m"]["currency"] is None
