"""Blackbox scan rerun: idempotent detection + evidence archiving.

rerun 场景：白盒+黑盒跑完后，基于已有白盒结果整体重跑黑盒。
- detect_blackbox_completed: 判断是否已跑过黑盒（evidence 文件存在）
- archive_blackbox_deliverables: --rerun 时把旧黑盒产出物归档到 .blackbox-archive/<run_ts>/

幂等信号用 evidence 文件存在性（黑盒 session.json 是 MetricsTracker 写的 nested
session.status，无 top-level status；evidence 文件最直接可靠）。
归档文件清单复用 session.py 的 bb_deliverable_patterns。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from shannon_core.session import BB_DELIVERABLE_PATTERNS  # 见 Step 3b


def detect_blackbox_completed(deliverables: Path) -> bool:
    """Return True if any `*_exploitation_evidence.md` exists in deliverables."""
    return bool(list(deliverables.glob("*_exploitation_evidence.md")))


def archive_blackbox_deliverables(deliverables: Path, run_ts: str) -> Path:
    """Move blackbox deliverables (evidence/findings/report) to a dated archive dir.

    归档清单复用 bb_deliverable_patterns。白盒产出物（analysis_deliverable 等）不归档。
    返回归档目录 deliverables/.blackbox-archive/<run_ts>/。
    """
    archive = deliverables / ".blackbox-archive" / run_ts
    archive.mkdir(parents=True, exist_ok=True)
    for pattern in BB_DELIVERABLE_PATTERNS:
        for src in deliverables.glob(pattern):
            shutil.move(str(src), str(archive / src.name))
    return archive
