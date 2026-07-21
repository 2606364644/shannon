"""
Message stream processor for Claude Agent SDK events.

Discriminates SDK messages by class (isinstance): AssistantMessage / UserMessage /
ResultMessage. Tool use and tool result are content blocks inside those messages,
not standalone events. Dispatch does real-time turn counting, text collection,
and spending cap detection. Aligned with TS message-handlers.ts capabilities.
"""

from __future__ import annotations

from typing import Any, Callable

from claude_agent_sdk import (
    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock,
    UserMessage, ToolResultBlock,
)

from .tool_audit_logger import NullToolAuditLogger, ToolAuditLogger

SPENDING_CAP_PATTERNS = [
    "spending limit",
    "credit limit",
    "quota exceeded",
    "budget exceeded",
    "maximum spend",
]


class MessageDispatcher:
    """Processes Claude Agent SDK streaming events."""

    def __init__(
        self,
        audit_logger: ToolAuditLogger | None = None,
        progress_callback: Callable[[str], None] | None = None,
        error_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.turn_count = 0
        self.text_parts: list[str] = []
        self.spending_cap_detected = False
        self.audit_logger: ToolAuditLogger = audit_logger or NullToolAuditLogger()
        self._progress = progress_callback
        self._on_error = error_callback
        # L1: ResultMessage-level metadata collected in _handle_result_message
        self.result_is_error: bool = False
        self.result_subtype: str | None = None
        self.stop_reason: str | None = None
        self.permission_denials: list | None = None
        self.api_error_status: int | None = None
        self.result_errors: list[str] | None = None

    async def dispatch(self, event: Any) -> str:
        """Dispatch a single SDK event. Returns 'continue' or 'complete'.

        claude_agent_sdk messages are discriminated by class (isinstance), NOT by
        a `.type` string field — AssistantMessage/UserMessage/SystemMessage have no
        `type` attribute. Tool use/result are content blocks inside messages, not
        top-level events.
        """
        if isinstance(event, ResultMessage):
            await self._handle_result_message(event)
            return "complete"

        if isinstance(event, AssistantMessage):
            return await self._handle_assistant(event)

        if isinstance(event, UserMessage):
            for block in getattr(event, "content", None) or []:
                if isinstance(block, ToolResultBlock):
                    await self.audit_logger.log_tool_end(getattr(block, "content", ""))
            return "continue"

        # SystemMessage / HookEventMessage / StreamEvent / unknown: ignored
        return "continue"

    async def _handle_assistant(self, event: AssistantMessage) -> str:
        self.turn_count += 1
        turn_text = ""
        for block in getattr(event, "content", None) or []:
            if isinstance(block, TextBlock):
                text = block.text
                self.text_parts.append(text)
                turn_text += text
                if self._is_spending_cap_in_text(text):
                    self.spending_cap_detected = True
            elif isinstance(block, ToolUseBlock):
                await self.audit_logger.log_tool_start(block.name, block.input)
                if self._progress:
                    self._progress(f"tool: {block.name}")
        if turn_text:
            await self.audit_logger.log_assistant_turn(self.turn_count, turn_text)
        error = getattr(event, "error", None)
        if error and self._on_error:
            self._on_error(str(error))
        return "continue"

    async def _handle_result_message(self, event: ResultMessage) -> None:
        """Collect result-level metadata from the terminal ResultMessage.

        Reading happens here (not in the Provider) so the data is available even
        on the empty-ResultMessage fallback path, and stays consistent with how
        ``collected_text`` / ``turn_count`` / ``spending_cap_detected`` are gathered.
        """
        self.result_is_error = getattr(event, "is_error", False)
        self.result_subtype = getattr(event, "subtype", None)
        self.stop_reason = getattr(event, "stop_reason", None)
        self.permission_denials = getattr(event, "permission_denials", None)
        self.api_error_status = getattr(event, "api_error_status", None)
        self.result_errors = getattr(event, "errors", None)

    @staticmethod
    def _is_spending_cap_in_text(text: str) -> bool:
        text_lower = text.lower()
        return any(kw in text_lower for kw in SPENDING_CAP_PATTERNS)

    @property
    def collected_text(self) -> str:
        return "".join(self.text_parts)
