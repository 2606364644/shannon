"""Atomic, workspace-scoped persistence for topology analysis jobs."""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAFE_WS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAFE_ID = re.compile(r"^topology-[a-f0-9]{12}$")
_ACTIVE_STATUSES = {"queued", "running"}


class TopologyAnalysisStore:
    def __init__(self, workspaces_dir: Path):
        self._root = Path(workspaces_dir).resolve()

    def _dir(self, ws: str, analysis_id: str | None = None) -> Path:
        if not _SAFE_WS.fullmatch(ws or ""):
            raise ValueError(f"invalid workspace: {ws!r}")
        base = (self._root / ws / "correlation-topology" / "analyses").resolve()
        if not base.is_relative_to(self._root):
            raise ValueError("workspace escapes storage root")
        if analysis_id is None:
            return base
        if not _SAFE_ID.fullmatch(analysis_id):
            raise ValueError(f"invalid analysis id: {analysis_id!r}")
        path = (base / analysis_id).resolve()
        if not path.is_relative_to(base):
            raise ValueError("analysis id escapes storage root")
        return path

    def path(self, ws: str, analysis_id: str) -> Path:
        """Validated analysis directory (public manager seam)."""
        return self._dir(ws, analysis_id)

    def create(self, ws: str, state: dict[str, Any]) -> dict[str, Any]:
        path = self._dir(ws, state.get("analysis_id", ""))
        path.mkdir(parents=True, exist_ok=False)
        self.write(state)
        return self.get(ws, state["analysis_id"])

    def write(self, state: dict[str, Any]) -> None:
        ws = state.get("workspace")
        analysis_id = state.get("analysis_id")
        path = self._dir(ws, analysis_id)
        path.mkdir(parents=True, exist_ok=True)
        target = path / "state.json"
        fd, temp_name = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_name, target)
        finally:
            Path(temp_name).unlink(missing_ok=True)

    def get(self, ws: str, analysis_id: str) -> dict[str, Any] | None:
        try:
            target = self._dir(ws, analysis_id) / "state.json"
        except ValueError:
            return None
        if not target.exists():
            return None
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if data.get("workspace") == ws else None

    def list(self, ws: str) -> list[dict[str, Any]]:
        base = self._dir(ws)
        if not base.exists():
            return []
        out: list[dict[str, Any]] = []
        for path in base.iterdir():
            if not path.is_dir():
                continue
            state = self.get(ws, path.name)
            if state is not None:
                out.append(state)
        return sorted(out, key=lambda item: (item.get("updated_at", ""), item.get("analysis_id", "")))

    def find_cached(self, ws: str, fingerprint: str, *, ttl_seconds: int) -> dict[str, Any] | None:
        if ttl_seconds <= 0:
            return None
        now = datetime.now(timezone.utc)
        for state in self.list(ws):
            if state.get("status") != "completed" or state.get("fingerprint") != fingerprint:
                continue
            try:
                updated = datetime.fromisoformat(state.get("completed_at", state.get("updated_at", "")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if (now - updated).total_seconds() <= ttl_seconds:
                return state
        return None

    def recover_interrupted(self) -> list[str]:
        recovered: list[str] = []
        if not self._root.exists():
            return recovered
        for workspace in sorted(p.name for p in self._root.iterdir() if p.is_dir() and _SAFE_WS.fullmatch(p.name)):
            for state in self.list(workspace):
                if state.get("status") not in _ACTIVE_STATUSES:
                    continue
                state["status"] = "interrupted"
                state["error"] = {
                    "code": "interrupted", "message": "web service restarted during analysis",
                    "retryable": True,
                }
                state["updated_at"] = _now_iso()
                self.write(state)
                recovered.append(state["analysis_id"])
        return recovered

    def cleanup(self, *, max_records: int = 100) -> None:
        if not self._root.exists() or max_records < 1:
            return
        for workspace in sorted(p.name for p in self._root.iterdir() if p.is_dir() and _SAFE_WS.fullmatch(p.name)):
            states = self.list(workspace)
            excess = len(states) - max_records
            if excess <= 0:
                continue
            removable = sorted(
                (state for state in states if state.get("status") not in _ACTIVE_STATUSES),
                key=lambda state: (state.get("updated_at", ""), state.get("analysis_id", "")),
            )[:excess]
            for state in removable:
                path = self._dir(workspace, state.get("analysis_id", ""))
                if path.exists() and path.is_relative_to(self._dir(workspace)):
                    _remove_tree(path)


def _remove_tree(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            _remove_tree(child)
        else:
            child.unlink(missing_ok=True)
    path.rmdir()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["TopologyAnalysisStore"]
