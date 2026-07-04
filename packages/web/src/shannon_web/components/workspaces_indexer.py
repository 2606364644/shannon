from __future__ import annotations

import os
from pathlib import Path

from shannon_core.session import SessionManager
from shannon_core.workspace import get_workspace_vuln_counts


class WorkspacesIndexer:
    def __init__(self, workspaces_dir: Path) -> None:
        self._dir = Path(workspaces_dir)
        self._active_pids: dict[str, int] = {}

    def set_active_pid(self, ws: str, pid: int | None) -> None:
        if pid is None:
            self._active_pids.pop(ws, None)
        else:
            self._active_pids[ws] = pid

    def sync_active(self, pids: dict[str, int]) -> None:
        """ScanManager 每次 list 时注入当前在跑 pid 表（替换式，避免 stale）。"""
        self._active_pids = dict(pids)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return False

    def is_running(self, ws: str) -> bool:
        """公开访问器：ws 是否有 alive pid（替换外部对 _active_pids/_pid_alive 的私有 reach）。"""
        pid = self._active_pids.get(ws)
        return pid is not None and self._pid_alive(pid)

    def _status_of(self, ws_name: str, session_status: str | None) -> str:
        if session_status == "completed":
            return "completed"
        if session_status == "failed":
            return "failed"
        alive = self.is_running(ws_name)
        if alive:
            return "running"
        if session_status == "running":
            return "running" if alive else "interrupted"
        return "interrupted"  # 无 scan_end 且 pid 不在 = 未正常结束

    def list_workspaces(self) -> list[dict]:
        mgr = SessionManager(self._dir)
        out: list[dict] = []
        for ws_path in mgr.list_workspaces():
            name = ws_path.name
            try:
                mgr.get_session_data(ws_path)  # 触发读，失败则跳过
            except Exception:
                continue
            scan_type = mgr.get_scan_type(ws_path)
            status = self._status_of(name, mgr.get_status(ws_path))
            try:
                vuln = get_workspace_vuln_counts(ws_path)
            except Exception:
                vuln = {}
            out.append({
                "name": name,
                "scan_type": scan_type,
                "status": status,
                "vuln_counts": vuln,
                "created_at": mgr.get_created_at(ws_path),
                "completed_at": mgr.get_completed_at(ws_path),
                "is_correlation": scan_type == "correlation",
            })
        out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return out
