from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .session import AuditSession


class AuditLogger(ABC):
    """Audit logging bridge for the AI execution layer. Callers never need null checks."""

    @abstractmethod
    async def log_llm_response(self, turn: int, content: str) -> None: ...

    @abstractmethod
    async def log_tool_start(self, tool_name: str, parameters: Any) -> None: ...

    @abstractmethod
    async def log_tool_end(self, result: Any) -> None: ...

    @abstractmethod
    async def log_error(self, error: Exception, duration: int, turns: int) -> None: ...


class RealAuditLogger(AuditLogger):
    """Bridges to AuditSession for actual logging."""

    def __init__(self, audit_session: AuditSession) -> None:
        self._session = audit_session

    async def log_llm_response(self, turn: int, content: str) -> None:
        await self._session.log_event("llm_response", {"turn": turn, "content": content})

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        await self._session.log_event("tool_start", {"toolName": tool_name, "parameters": parameters})

    async def log_tool_end(self, result: Any) -> None:
        await self._session.log_event("tool_end", result)

    async def log_error(self, error: Exception, duration: int, turns: int) -> None:
        await self._session.log_event("error", {
            "message": str(error),
            "errorType": type(error).__name__,
            "duration": duration,
            "turns": turns,
        })


class NullAuditLogger(AuditLogger):
    """No-op implementation. All methods are safe to call without effect."""

    async def log_llm_response(self, turn: int, content: str) -> None: pass
    async def log_tool_start(self, tool_name: str, parameters: Any) -> None: pass
    async def log_tool_end(self, result: Any) -> None: pass
    async def log_error(self, error: Exception, duration: int, turns: int) -> None: pass


def create_audit_logger(audit_session: AuditSession | None) -> AuditLogger:
    """Factory: returns RealAuditLogger if session exists, NullAuditLogger otherwise."""
    if audit_session is not None:
        return RealAuditLogger(audit_session)
    return NullAuditLogger()
