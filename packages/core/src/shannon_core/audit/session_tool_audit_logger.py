"""SessionToolAuditLogger — bridges core ToolAuditLogger events to AuditSession.

Holds per-agent state (agent_name + AgentLogger) so concurrent agents never race
on shared session fields. The MessageDispatcher/StreamCollector call
log_assistant_turn/log_tool_start/etc.; this logger routes each event to its own
per-agent JSON log AND to the shared workflow log with explicit agent attribution.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shannon_core.agents.tool_audit_logger import ToolAuditLogger
from shannon_core.audit.agent_logger import AgentLogger

if TYPE_CHECKING:
    from .session import AuditSession


class SessionToolAuditLogger(ToolAuditLogger):
    def __init__(self, session: "AuditSession", agent_name: str, attempt: int = 1) -> None:
        self._session = session
        self._agent_name = agent_name
        self._agent_logger = AgentLogger(session._meta, agent_name, attempt)

    async def initialize(self) -> None:
        """Open the per-agent JSON log and write its header + agent_start event."""
        await self._agent_logger.initialize()

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        await self._agent_logger.log_event("tool_start", {"toolName": tool_name, "parameters": parameters})
        await self._session.log_tool_call(self._agent_name, tool_name, parameters)

    async def log_tool_end(self, result: Any) -> None:
        # tool_end has no workflow.log surface; recorded to the per-agent JSON only.
        await self._agent_logger.log_event("tool_end", {"result": str(result)[:200]})

    async def log_assistant_turn(self, turn: int, content: str) -> None:
        await self._agent_logger.log_event("llm_response", {"turn": turn, "content": content})
        await self._session.log_llm_turn(self._agent_name, turn, content)

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        await self._session.log_error(
            RuntimeError(error), context=f"turn={turn_count}, {duration_ms}ms")

    async def close(self, success: bool, duration_ms: int) -> None:
        """Write agent_end to the per-agent JSON log and close its stream."""
        await self._agent_logger.log_event("agent_end", {"success": success, "duration_ms": duration_ms})
        await self._agent_logger.close()
