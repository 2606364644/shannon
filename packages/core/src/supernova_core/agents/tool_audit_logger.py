"""
Tool call audit logger — Null Object pattern.

Provides an ABC for tool call auditing with a no-op default implementation
so callers never need to null-check the logger.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supernova_core.logging.activity_logger import ActivityLogger


class ToolAuditLogger(ABC):
    """Tool call audit logger interface."""

    @abstractmethod
    async def log_tool_start(self, tool_name: str, parameters: Any) -> None: ...

    @abstractmethod
    async def log_tool_end(self, result: Any) -> None: ...

    @abstractmethod
    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None: ...

    @abstractmethod
    async def log_assistant_turn(self, turn: int, content: str) -> None: ...


class NullToolAuditLogger(ToolAuditLogger):
    """No-op implementation — safe default when auditing is disabled."""

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        pass

    async def log_tool_end(self, result: Any) -> None:
        pass

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        pass

    async def log_assistant_turn(self, turn: int, content: str) -> None:
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

    async def log_assistant_turn(self, turn: int, content: str) -> None:
        self._logger.info("assistant_turn", turn=turn, content=content[:500])


class BufferingToolAuditLogger(ToolAuditLogger):
    """装饰器：转发全部事件到 inner，同时缓冲原始 tool_start 轨迹。

    executor 用它在 agent 运行期间无损收集工具轨迹（inner 可能是
    ActivityToolAuditLogger——其 parameters 会 str() 截断，缓冲必须先于转发
    取原始值），落盘 verdicts.json 时读 tool_events 做端点痕迹匹配
    （spec 2026-09-03-blackbox-verification-gap-traceability §4）。
    inner=None 时纯缓冲（调用方未传审计 logger 的路径）。
    """

    def __init__(self, inner: "ToolAuditLogger | None") -> None:
        self._inner = inner
        self.tool_events: list[dict] = []

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        self.tool_events.append({"tool_name": tool_name, "parameters": parameters})
        if self._inner is not None:
            await self._inner.log_tool_start(tool_name, parameters)

    async def log_tool_end(self, result: Any) -> None:
        if self._inner is not None:
            await self._inner.log_tool_end(result)

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        if self._inner is not None:
            await self._inner.log_error(error, turn_count=turn_count, duration_ms=duration_ms)

    async def log_assistant_turn(self, turn: int, content: str) -> None:
        if self._inner is not None:
            await self._inner.log_assistant_turn(turn, content)