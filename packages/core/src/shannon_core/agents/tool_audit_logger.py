"""
Tool call audit logger — Null Object pattern.

Provides an ABC for tool call auditing with a no-op default implementation
so callers never need to null-check the logger.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shannon_core.logging.activity_logger import ActivityLogger


class ToolAuditLogger(ABC):
    """Tool call audit logger interface."""

    @abstractmethod
    async def log_tool_start(self, tool_name: str, parameters: Any) -> None: ...

    @abstractmethod
    async def log_tool_end(self, result: Any) -> None: ...

    @abstractmethod
    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None: ...


class NullToolAuditLogger(ToolAuditLogger):
    """No-op implementation — safe default when auditing is disabled."""

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        pass

    async def log_tool_end(self, result: Any) -> None:
        pass

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        pass


class ActivityToolAuditLogger(ToolAuditLogger):
    """Bridges tool audit events to ActivityLogger."""

    def __init__(self, logger: ActivityLogger) -> None:
        self._logger = logger

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        self._logger.info("tool_start", tool_name=tool_name, parameters=str(parameters)[:500])

    async def log_tool_end(self, result: Any) -> None:
        self._logger.info("tool_end", result=str(result)[:500])

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        self._logger.error("agent_error", error=error, turn_count=turn_count, duration_ms=duration_ms)