"""Blackbox scan rerun: idempotent detection + evidence archiving.

rerun 场景：白盒+黑盒跑完后，基于已有白盒结果整体重跑黑盒。
- detect_blackbox_completed: 判断是否已跑过黑盒（evidence 文件存在）
- archive_blackbox_deliverables: --rerun 时把旧黑盒产出物归档到 .blackbox-archive/<run_ts>/

幂等信号用 evidence 文件存在性（黑盒 session.json 是 MetricsTracker 写的 nested
session.status，无 top-level status；evidence 文件最直接可靠）。
归档文件清单 BB_DELIVERABLE_PATTERNS 本地定义（删 clean 后只剩 rerun 用，
放 core session.py 名不副实）。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from shannon_core.utils.paths import BLACKBOX_SUBDIR

# Blackbox deliverable filename patterns (glob). 归档清单
# （archive_blackbox_deliverables 用）。删 clean 后只剩 rerun 用。
BB_DELIVERABLE_PATTERNS: list[str] = [
    "*_exploitation_evidence.md",
    "*_findings.md",
    "comprehensive_security_assessment_report.md",
]


def detect_blackbox_completed(deliverables: Path) -> bool:
    """Return True if any `*_exploitation_evidence.md` exists in blackbox/ subdir."""
    return bool(list((deliverables / BLACKBOX_SUBDIR).glob("*_exploitation_evidence.md")))


def archive_blackbox_deliverables(deliverables: Path, run_ts: str) -> Path:
    """Move blackbox deliverables (evidence/findings/report) to a dated archive dir.

    归档清单复用 bb_deliverable_patterns。白盒产出物（analysis_deliverable 等）不归档。
    归档源与目标都在 deliverables/blackbox/ 内（黑盒产出物隔离）。
    返回归档目录 deliverables/blackbox/.blackbox-archive/<run_ts>/。
    """
    bb = deliverables / BLACKBOX_SUBDIR
    archive = bb / ".blackbox-archive" / run_ts
    archive.mkdir(parents=True, exist_ok=True)
    for pattern in BB_DELIVERABLE_PATTERNS:
        for src in bb.glob(pattern):
            dest = archive / src.name
            if dest.exists():
                # 同 run_ts 下重名（秒级时间戳双 rerun / 测试复用 run_ts）：
                # 加序号后缀避免覆盖丢历史
                stem, suffix = src.stem, src.suffix
                i = 1
                while dest.exists():
                    dest = archive / f"{stem}_{i}{suffix}"
                    i += 1
            shutil.move(str(src), str(dest))
    return archive
