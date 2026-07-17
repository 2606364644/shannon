# packages/core/src/shannon_core/code_index/gitnexus_track_status.py
"""GitNexus 轨 per-class 状态产物(fail-fast 编排用)。

workflow 写、merger/report 读。纯函数,不 import GitNexus/确定性层符号。
铁律:本产物只给 workflow/merger/report 编排用,绝不喂 LLM 轨 prompt。
"""
from __future__ import annotations

import json
from pathlib import Path

FILENAME = "gitnexus_track_status.json"


def write_track_status(deliverables: Path, statuses: dict) -> None:
    """原子写 per-class 状态。statuses = {vc: {"status":"ok"|"failed", ...}}。"""
    path = Path(deliverables) / FILENAME
    path.write_text(json.dumps(statuses, ensure_ascii=False, indent=2), encoding="utf-8")


def read_track_status(deliverables: Path) -> dict:
    """读 per-class 状态;文件缺/损坏返 {}(不抛,merger/report 容错)。"""
    path = Path(deliverables) / FILENAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
