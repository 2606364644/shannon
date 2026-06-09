"""Tests for message_dispatcher module."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from claude_agent_sdk import ResultMessage

from shannon_core.agents.message_dispatcher import (
    SPENDING_CAP_PATTERNS,
    MessageDispatcher,
)
from shannon_core.agents.tool_audit_logger import NullToolAuditLogger


def _make_text_event(text: str) -> object:
    """Create a minimal text-type event."""
    event = MagicMock()
    event.type = "text"
    event.text = text
    return event


def _make_assistant_event(
    texts: list[str] | None = None,
    error: str | None = None,
) -> object:
    """Create a minimal assistant-type event."""
    event = MagicMock()
    event.type = "assistant"
    blocks = []
    for t in (texts or []):
        block = MagicMock()
        block.text = t
        blocks.append(block)
    event.content = blocks
    event.error = error
    return event


def _make_tool_use_event(name: str = "bash", input_params: dict | None = None) -> object:
    """Create a minimal tool_use-type event."""
    event = MagicMock()
    event.type = "tool_use"
    event.name = name
    event.input = input_params or {"command": "ls"}
    return event


def _make_tool_result_event(content: str = "file.txt") -> object:
    """Create a minimal tool_result-type event."""
    event = MagicMock()
    event.type = "tool_result"
    event.content = content
    return event


class TestMessageDispatcherDefaults:
    """Default construction and initial state."""

    def test_initial_state(self):
        d = MessageDispatcher()
        assert d.turn_count == 0
        assert d.collected_text == ""
        assert d.spending_cap_detected is False

    def test_default_audit_logger_is_null(self):
        d = MessageDispatcher()
        assert isinstance(d.audit_logger, NullToolAuditLogger)


class TestTextCollection:
    """Text events and assistant text blocks accumulate."""

    @pytest.mark.asyncio
    async def test_text_event_appends(self):
        d = MessageDispatcher()
        result = await d.dispatch(_make_text_event("hello "))
        assert result == "continue"
        assert d.collected_text == "hello "

    @pytest.mark.asyncio
    async def test_multiple_text_events(self):
        d = MessageDispatcher()
        await d.dispatch(_make_text_event("hello "))
        await d.dispatch(_make_text_event("world"))
        assert d.collected_text == "hello world"

    @pytest.mark.asyncio
    async def test_assistant_event_collects_text(self):
        d = MessageDispatcher()
        event = _make_assistant_event(texts=["response text"])
        result = await d.dispatch(event)
        assert result == "continue"
        assert d.collected_text == "response text"

    @pytest.mark.asyncio
    async def test_assistant_event_increments_turn_count(self):
        d = MessageDispatcher()
        await d.dispatch(_make_assistant_event(texts=["one"]))
        assert d.turn_count == 1
        await d.dispatch(_make_assistant_event(texts=["two"]))
        assert d.turn_count == 2


class TestResultMessage:
    """ResultMessage events signal completion."""

    @pytest.mark.asyncio
    async def test_result_message_returns_complete(self):
        d = MessageDispatcher()
        msg = ResultMessage(
            subtype="result",
            duration_ms=1000,
            duration_api_ms=500,
            is_error=False,
            num_turns=1,
            session_id="test",
        )
        result = await d.dispatch(msg)
        assert result == "complete"


class TestToolEvents:
    """Tool use and tool result events delegate to audit logger."""

    @pytest.mark.asyncio
    async def test_tool_use_calls_audit_logger(self):
        mock_logger = AsyncMock()
        d = MessageDispatcher(audit_logger=mock_logger)
        event = _make_tool_use_event("bash", {"command": "ls"})
        result = await d.dispatch(event)
        assert result == "continue"
        mock_logger.log_tool_start.assert_awaited_once_with("bash", {"command": "ls"})

    @pytest.mark.asyncio
    async def test_tool_result_calls_audit_logger(self):
        mock_logger = AsyncMock()
        d = MessageDispatcher(audit_logger=mock_logger)
        event = _make_tool_result_event("file.txt")
        result = await d.dispatch(event)
        assert result == "continue"
        mock_logger.log_tool_end.assert_awaited_once_with("file.txt")

    @pytest.mark.asyncio
    async def test_tool_use_progress_callback(self):
        progress = MagicMock()
        d = MessageDispatcher(progress_callback=progress)
        await d.dispatch(_make_tool_use_event("edit"))
        progress.assert_called_once_with("tool: edit")


class TestSpendingCapDetection:
    """Layer 1: message-level keyword detection in assistant text."""

    @pytest.mark.asyncio
    async def test_detects_spending_limit(self):
        d = MessageDispatcher()
        await d.dispatch(_make_assistant_event(texts=["your spending limit has been reached"]))
        assert d.spending_cap_detected is True

    @pytest.mark.asyncio
    async def test_detects_credit_limit(self):
        d = MessageDispatcher()
        await d.dispatch(_make_assistant_event(texts=["credit limit exceeded"]))
        assert d.spending_cap_detected is True

    @pytest.mark.asyncio
    async def test_detects_quota_exceeded(self):
        d = MessageDispatcher()
        await d.dispatch(_make_assistant_event(texts=["quota exceeded for this account"]))
        assert d.spending_cap_detected is True

    @pytest.mark.asyncio
    async def test_normal_text_no_detection(self):
        d = MessageDispatcher()
        await d.dispatch(_make_assistant_event(texts=["here is your code review"]))
        assert d.spending_cap_detected is False

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        d = MessageDispatcher()
        await d.dispatch(_make_assistant_event(texts=["SPENDING LIMIT REACHED"]))
        assert d.spending_cap_detected is True


class TestErrorCallback:
    """Error callback fires on assistant events with errors."""

    @pytest.mark.asyncio
    async def test_error_callback_invoked(self):
        errors = []
        d = MessageDispatcher(error_callback=lambda e: errors.append(e))
        await d.dispatch(_make_assistant_event(error="rate limited"))
        assert errors == ["rate limited"]

    @pytest.mark.asyncio
    async def test_no_error_callback_when_none(self):
        d = MessageDispatcher()
        # Should not raise
        await d.dispatch(_make_assistant_event(error="something"))


class TestUnknownEvents:
    """Unknown event types are silently continued."""

    @pytest.mark.asyncio
    async def test_unknown_event_type(self):
        d = MessageDispatcher()
        event = MagicMock()
        event.type = "something_else"
        result = await d.dispatch(event)
        assert result == "continue"


class TestSpendingCapPatterns:
    """Spending cap keyword list is correct."""

    def test_patterns_list(self):
        assert "spending limit" in SPENDING_CAP_PATTERNS
        assert "credit limit" in SPENDING_CAP_PATTERNS
        assert "quota exceeded" in SPENDING_CAP_PATTERNS
        assert "budget exceeded" in SPENDING_CAP_PATTERNS
        assert "maximum spend" in SPENDING_CAP_PATTERNS


class TestResultMessageMetadata:
    """L1: ResultMessage events collect result-level metadata before signalling completion."""

    @pytest.mark.asyncio
    async def test_collects_success_metadata(self):
        d = MessageDispatcher()
        msg = ResultMessage(
            subtype="result",
            duration_ms=1000,
            duration_api_ms=500,
            is_error=False,
            num_turns=1,
            session_id="test",
            stop_reason="end_turn",
            total_cost_usd=0.001,
        )
        result = await d.dispatch(msg)
        assert result == "complete"
        assert d.result_is_error is False
        assert d.result_subtype == "result"
        assert d.stop_reason == "end_turn"
        assert d.permission_denials is None
        assert d.api_error_status is None
        assert d.result_errors is None

    @pytest.mark.asyncio
    async def test_collects_failure_metadata(self):
        d = MessageDispatcher()
        msg = ResultMessage(
            subtype="error_max_turns",
            duration_ms=1000,
            duration_api_ms=500,
            is_error=True,
            num_turns=200,
            session_id="test",
            stop_reason="end_turn",
            permission_denials=[{"tool": "bash"}],
            api_error_status=429,
            errors=["rate limited", "max turns"],
        )
        result = await d.dispatch(msg)
        assert result == "complete"
        assert d.result_is_error is True
        assert d.result_subtype == "error_max_turns"
        assert d.permission_denials == [{"tool": "bash"}]
        assert d.api_error_status == 429
        assert d.result_errors == ["rate limited", "max turns"]

    def test_default_metadata_state(self):
        d = MessageDispatcher()
        assert d.result_is_error is False
        assert d.result_subtype is None
        assert d.stop_reason is None
        assert d.permission_denials is None
        assert d.api_error_status is None
        assert d.result_errors is None
