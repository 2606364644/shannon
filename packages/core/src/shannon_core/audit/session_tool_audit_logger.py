"""SessionToolAuditLogger — bridges core ToolAuditLogger events to AuditSession.

MessageDispatcher (inside provider.call) calls log_tool_start / log_tool_end /
log_error / log_assistant_turn. Routing them through AuditSession feeds the
display pipeline (WorkflowLogger -> dispatcher -> renderers), so tool/llm
events appear in workflow.log and on the live dashboard.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shannon_core.agents.tool_audit_logger import ToolAuditLogger

if TYPE_CHECKING:
    from .session import AuditSession


class SessionToolAuditLogger(ToolAuditLogger):
    def __init__(self, session: "AuditSession") -> None:
        self._session = session

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        await self._session.log_event(
            "tool_start", {"toolName": tool_name, "parameters": parameters})

    async def log_tool_end(self, result: Any) -> None:
        # tool_end has no DisplayEvent/workflow.log surface; AuditSession.log_event
        # records it to the per-agent JSON log only (when an agent is active).
        await self._session.log_event("tool_end", {"result": str(result)[:200]})

    async def log_assistant_turn(self, turn: int, content: str) -> None:
        await self._session.log_event(
            "llm_response", {"turn": turn, "content": content})

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        await self._session.log_error(
            RuntimeError(error), context=f"turn={turn_count}, {duration_ms}ms")
