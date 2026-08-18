# packages/core/src/supernova_core/code_index/gitnexus_track_status.py
"""GitNexus 轨 per-class 状态产物(fail-fast 编排用)。

workflow 写、merger/report 读。纯函数,不 import GitNexus/确定性层符号。
铁律:本产物只给 workflow/merger/report 编排用,绝不喂 LLM 轨 prompt。
"""
from __future__ import annotations

import json
from pathlib import Path

FILENAME = "gitnexus_track_status.json"


def write_track_status(deliverables: Path, statuses: dict) -> None:
    """原子写 per-class 状态。statuses = {vc: {"status":"ok"|"failed", ...}}。
    tiering（spec 2026-08-18）：落桶内 intermediate/。"""
    from supernova_core.utils.paths import intermediate_path
    path = intermediate_path(Path(deliverables), FILENAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(statuses, ensure_ascii=False, indent=2), encoding="utf-8")


def read_track_status(deliverables: Path) -> dict:
    """读 per-class 状态;文件缺/损坏返 {}(不抛,merger/report 容错)。
    tiering 后先 intermediate/ 再顶层兜底（旧结构）。"""
    from supernova_core.utils.paths import resolve_intermediate
    path = resolve_intermediate(Path(deliverables), FILENAME)
    if path is None:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
