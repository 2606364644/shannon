from __future__ import annotations

import json
from pathlib import Path

from shannon_core.utils.paths import WHITEBOX_SUBDIR, resolve_track_deliverable
from shannon_core.workspace import _is_valid_queue_file, compute_deliverables_summary


class DeliverablesReader:
    """Read deliverables for a workspace, supporting both the new track-scoped
    layout (``deliverables/{whitebox,blackbox}/*``) and the legacy flat layout
    (``deliverables/*``). md / json / log reads + summary.
    """

    def __init__(self, workspace_path: Path) -> None:
        self._ws = Path(workspace_path)
        self._deliverables = self._ws / "deliverables"

    def summary(self, track: str = WHITEBOX_SUBDIR) -> dict:
        """Summarize vuln queues + reports.

        委托 core ``compute_deliverables_summary``（覆盖 legacy flat 布局），
        再补充 track 子目录（新布局）的产物，使两种布局都能正确汇总。
        """
        base = compute_deliverables_summary(self._ws)
        vuln_queues: list[str] = list(base.get("vuln_queues", []))
        reports: list[str] = list(base.get("reports", []))

        track_dir = self._deliverables / track
        if track_dir.exists():
            # per-class exploitation queues under the track dir
            for f in sorted(track_dir.iterdir()):
                if not f.is_file():
                    continue
                if (
                    f.name.endswith("_exploitation_queue.json")
                    and _is_valid_queue_file(f)
                ):
                    vc = f.name.replace("_exploitation_queue.json", "")
                    if vc not in vuln_queues:
                        vuln_queues.append(vc)
                elif f.name.endswith(".md") and f.name not in reports:
                    reports.append(f.name)

        return {"vuln_queues": vuln_queues, "reports": reports}

    def read(self, filename: str, track: str = WHITEBOX_SUBDIR) -> dict | list | str:
        p = resolve_track_deliverable(self._deliverables, track, filename)
        if not p.exists():
            raise FileNotFoundError(filename)
        text = p.read_text("utf-8")
        if p.suffix == ".json":
            return json.loads(text) if text.strip() else []
        return text

    def read_log(self, name: str = "workflow.log") -> str:
        p = self._ws / name
        if not p.exists():
            p = self._ws / "agents" / name  # 兼容 agents/*.log
        if not p.exists():
            raise FileNotFoundError(name)
        return p.read_text("utf-8")
