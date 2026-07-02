from __future__ import annotations

import json
from pathlib import Path

from shannon_core.utils.paths import WHITEBOX_SUBDIR, resolve_track_deliverable
from shannon_core.workspace import compute_deliverables_summary


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

        Pure delegation to core ``compute_deliverables_summary`` (which now scans
        the deliverables dir recursively, covering both the track-scoped and
        legacy flat layouts). The ``track`` arg is retained for API compatibility
        but no longer augments the result — core handles all layouts uniformly.
        """
        return compute_deliverables_summary(self._ws)

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
