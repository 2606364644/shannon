"""Process-wide registry for the current scan's AuditSession.

Activities reach the live display via get_audit_session(). The driver
(run_scan) sets the singleton before starting the worker and clears it after.

NullAuditSession is the safe default when no scan is active (tests, standalone
tooling): every method is a no-op so callers never null-check.
"""
from __future__ import annotations

from typing import Any

_current: "Any" = None


class NullAuditSession:
    """No-op AuditSession stand-in. Safe to call without initialize()."""

    async def initialize(self, workflow_id: str | None = None) -> None: pass
    async def start_agent(self, agent_name: str, prompt: str, attempt: int = 1) -> None: pass
    async def end_agent(self, agent_name: str, result: Any) -> None: pass
    async def log_event(self, event_type: str, event_data: Any) -> None: pass
    async def log_phase_start(self, phase: str) -> None: pass
    async def log_phase_complete(self, phase: str) -> None: pass
    async def log_workflow_complete(self, summary: Any) -> None: pass
    async def log_error(self, error: Any, context: str | None = None, *,
                        attempt: int | None = None,
                        max_attempts: int | None = None) -> None: pass
    async def log_resume_header(self, resume_info: Any) -> None: pass
    async def update_session_status(self, status: str) -> None: pass
    async def close(self) -> None: pass


def set_audit_session(session: Any) -> None:
    global _current
    _current = session


def get_audit_session() -> Any:
    return _current if _current is not None else NullAuditSession()


def clear_audit_session() -> None:
    global _current
    _current = None
