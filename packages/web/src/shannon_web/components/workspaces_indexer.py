from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from shannon_core.session import SessionManager
from shannon_core.workspace import get_workspace_vuln_counts

from .scan_liveness import is_scan_recently_active


def _to_unix(v) -> float | None:
    """归一 created_at/completed_at 为 unix float|None:float|int 直用、ISO str 解析、None/异常→None。
    前端 Workspace.created_at/SessionData.created_at 期望 number(unix);修后端透传 ISO str
    致前端 new Date(unix*1000) Invalid Date 的契约断裂。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _created_at_key(row: dict) -> float:
    """sort key:复用 _to_unix,缺失/异常→0.0(排最后)。
    修 sort 时 float 与 None-fallback-str 不可比、曾致 /api/workspaces 500 的 bug。"""
    return _to_unix(row.get("created_at")) or 0.0


_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "interrupted", "cancelled", "killed", "crashed"}
)


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

    def _status_of(self, ws_path: Path, session_status: str | None) -> str:
        # 终态优先(强信号,立即定):session.json 显式标了终态 → 该终态。取代旧「只认
        # completed/failed + 兜底推断 interrupted」的混乱(spec §4.3)。
        if session_status in _TERMINAL_STATUSES:
            return session_status
        # 判活靠 heartbeat mtime(pid 表不参与判活,只服务 cancel)。heartbeat fresh
        # → scan worker 进程仍存活(即便 web 看不到 host pid 也判活)→ running。
        # 回归:kol_mapping_service_20260708-193139 被误标 interrupted 即缺此判活信号源。
        if is_scan_recently_active(ws_path):
            return "running"
        # 无终态 + 无 fresh heartbeat = 未正常结束(死掉的孤儿/容器重启后子进程同死)。
        return "interrupted"

    def list_workspaces(self) -> list[dict]:
        mgr = SessionManager(self._dir)
        out: list[dict] = []
        for ws_path in mgr.list_workspaces():
            name = ws_path.name
            try:
                data = mgr.get_session_data(ws_path)
            except Exception:
                continue
            scan_type = mgr.get_scan_type(ws_path)
            status = self._status_of(ws_path, mgr.get_status(ws_path))
            try:
                vuln = get_workspace_vuln_counts(ws_path)
            except Exception:
                vuln = {}
            metrics = data.get("metrics", {}) if isinstance(data, dict) else {}
            out.append({
                "name": name,
                "scan_type": scan_type,
                "status": status,
                "vuln_counts": vuln,
                "vuln_count": sum(vuln.values()) if vuln else 0,
                "total_cost_usd": metrics.get("total_cost_usd"),
                "cost_currency": metrics.get("cost_currency"),
                "total_duration_ms": metrics.get("total_duration_ms"),
                "links": data.get("links", {}) if isinstance(data, dict) else {},
                "created_at": _to_unix(mgr.get_created_at(ws_path)),
                "completed_at": _to_unix(mgr.get_completed_at(ws_path)),
                "is_correlation": scan_type == "correlation",
            })
        out.sort(key=_created_at_key, reverse=True)
        return out
