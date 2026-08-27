"""step cache：LLM 重活步骤级自检（spec 2026-08-27-web-resume-breakpoint §4.3）。

marker（显式完成记录）+ 输入指纹（mtime_ns:size）+ outputs 清单 + 返回值快照。

- 完成信号是 marker 而非业务产物存在性：``mark_done`` 只在 activity 干净完成
  末尾调用，中途失败必无 marker → 整步重跑；marker 自身经 ``atomic_write_json``
  原子落盘，损坏解析失败即 fail-open。
- 双向校验：输入侧指纹匹配（上游重写 → mtime_ns 变 → 重跑；调用侧输入清单与
  记录不一致同样重跑）+ 输出侧存在校验（产物清单任一缺失 → 重跑重建）。
- 返回值快照：跳过时直接还原缓存返回值，workflow 下游判断逻辑不变。
- 一律 fail-open：任何异常/不满足都返回 ``(False, None)`` 走重跑，不阻塞扫描。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from supernova_core.utils.atomic_write import atomic_write_json
from supernova_core.utils.paths import INTERMEDIATE_SUBDIR

_STEP_CACHE_SUBDIR = ".step-cache"

# 已接自检的步骤名注册表（§4.3——仅这两个输入无人覆写的大窗口步骤）。
# resume-preview（§4.5）据此列出 steps 简表；新增接线步骤时在此登记。
STEP_AUTHZ_GITNEXUS_JUDGE = "authz-gitnexus-judge"
STEP_GITNEXUS_CHAIN_VERDICT = "gitnexus-chain-verdict"
KNOWN_STEPS = (STEP_AUTHZ_GITNEXUS_JUDGE, STEP_GITNEXUS_CHAIN_VERDICT)


def _marker_path(step: str, deliverables: Path) -> Path:
    return deliverables / INTERMEDIATE_SUBDIR / _STEP_CACHE_SUBDIR / f"{step}.json"


def _fingerprint(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return f"{st.st_mtime_ns}:{st.st_size}"


def mark_done(step: str, deliverables: Path, inputs: list[Path],
              outputs: list[Path], ret: dict | None = None,
              salt: str = "") -> None:
    """干净完成末尾打点：原子写 intermediate/.step-cache/{step}.json。

    salt：非文件依赖的指纹（如 env 开关组合）——调用侧跳过判定时传同值，
    值变化（如 SUPERNOVA_GITNEXUS_LLM_ENABLED 翻转）→ 不匹配 → 重跑。"""
    marker = _marker_path(step, deliverables)
    marker.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(marker, {
        "step": step,
        "ts": time.time(),
        "inputs": {str(p): _fingerprint(Path(p)) for p in inputs},
        "outputs": [str(p) for p in outputs],
        "ret": ret,
        "salt": salt,
    })


def should_skip(step: str, deliverables: Path,
                inputs: list[Path], salt: str = "") -> tuple[bool, dict | None]:
    """marker 可解析 ∧ 输入清单与指纹全匹配 ∧ salt 一致 ∧ outputs 全存在
    → (True, ret 快照)。

    任一不满足（marker 缺失/损坏、输入缺失或指纹变化、清单增减、salt 不一致、
    产物缺失、marker 非法形态）→ (False, None)，fail-open 到重跑。
    """
    try:
        return _should_skip(step, deliverables, inputs, salt)
    except Exception:  # noqa: BLE001 — fail-open：缓存层任何异常不阻塞扫描
        return (False, None)


def _should_skip(step: str, deliverables: Path, inputs: list[Path],
                 salt: str) -> tuple[bool, dict | None]:
    marker = _marker_path(step, deliverables)
    if not marker.exists():
        return (False, None)
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return (False, None)
    if not isinstance(data, dict):
        return (False, None)
    if data.get("salt", "") != salt:
        return (False, None)

    recorded: dict = data.get("inputs") or {}
    current = {str(p) for p in inputs}
    if set(recorded) != current:
        return (False, None)  # 输入清单增减 → 旧 marker 不覆盖新接线
    for path, fp in recorded.items():
        # 缺失→缺失（None==None）跳过（一致的缺失语义）；缺失→出现
        # （None≠指纹）或 指纹变化 → 重跑。
        if _fingerprint(Path(path)) != fp:
            return (False, None)

    for path in data.get("outputs") or []:
        if not Path(path).exists():
            return (False, None)

    return (True, data.get("ret"))


def preview_steps(deliverables: Path) -> list[dict]:
    """resume-preview（§4.5）：已知步骤的缓存状态简表（只读）。

    done = marker 存在且输入指纹此刻仍匹配且产物在盘（附 ts）；
    stale = marker 存在但输入已变化 / 产物缺失 / marker 损坏（附 reason）；
    missing = 无 marker（首跑未跑到或未接缓存）。
    """
    out: list[dict] = []
    for step in KNOWN_STEPS:
        marker = _marker_path(step, deliverables)
        if not marker.exists():
            out.append({"step": step, "state": "missing"})
            continue
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("marker 非法形态")
        except (json.JSONDecodeError, OSError, ValueError):
            out.append({"step": step, "state": "stale", "reason": "marker 损坏"})
            continue
        inputs_changed = any(
            _fingerprint(Path(p)) != fp
            for p, fp in (data.get("inputs") or {}).items())
        outputs_missing = any(
            not Path(p).exists() for p in data.get("outputs") or [])
        if inputs_changed:
            out.append({"step": step, "state": "stale", "reason": "输入已变化"})
        elif outputs_missing:
            out.append({"step": step, "state": "stale", "reason": "产物缺失"})
        else:
            out.append({"step": step, "state": "done", "ts": data.get("ts")})
    return out
