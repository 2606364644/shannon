"""Tests for tool_audit_logger module."""

import pytest
from unittest.mock import MagicMock

from shannon_core.agents.tool_audit_logger import (
    ActivityToolAuditLogger,
    NullToolAuditLogger,
    ToolAuditLogger,
)
from shannon_core.logging.activity_logger import ActivityLogger


class TestToolAuditLoggerInterface:
    """Verify the ABC contract."""

    def test_cannot_instantiate_abc(self):
        """ToolAuditLogger is abstract and cannot be instantiated directly."""
        with pytest.raises(TypeError):
            ToolAuditLogger()


class TestNullToolAuditLogger:
    """NullToolAuditLogger is a no-op implementation of ToolAuditLogger."""

    def test_is_subclass(self):
        assert issubclass(NullToolAuditLogger, ToolAuditLogger)

    @pytest.mark.asyncio
    async def test_log_tool_start_does_nothing(self):
        logger = NullToolAuditLogger()
        # Should not raise
        await logger.log_tool_start("bash", {"command": "ls"})

    @pytest.mark.asyncio
    async def test_log_tool_end_does_nothing(self):
        logger = NullToolAuditLogger()
        await logger.log_tool_end("output text")

    @pytest.mark.asyncio
    async def test_log_error_does_nothing(self):
        logger = NullToolAuditLogger()
        await logger.log_error("something broke", turn_count=3, duration_ms=500)


class TestActivityToolAuditLogger:
    """ActivityToolAuditLogger bridges to ActivityLogger."""

    def test_is_subclass(self):
        assert issubclass(ActivityToolAuditLogger, ToolAuditLogger)

    @pytest.mark.asyncio
    async def test_log_tool_start_delegates(self):
        mock_activity = MagicMock(spec=ActivityLogger)
        logger = ActivityToolAuditLogger(mock_activity)

        await logger.log_tool_start("bash", {"command": "ls -la"})

        mock_activity.info.assert_called_once()
        call_kwargs = mock_activity.info.call_args
        assert call_kwargs[0][0] == "tool_start"
        assert call_kwargs[1]["tool_name"] == "bash"
        # Parameters are stringified and truncated to 500 chars
        assert "ls -la" in call_kwargs[1]["parameters"]

    @pytest.mark.asyncio
    async def test_log_tool_end_delegates(self):
        mock_activity = MagicMock(spec=ActivityLogger)
        logger = ActivityToolAuditLogger(mock_activity)

        await logger.log_tool_end("file contents here")

        mock_activity.info.assert_called_once()
        call_kwargs = mock_activity.info.call_args
        assert call_kwargs[0][0] == "tool_end"
        assert "file contents here" in call_kwargs[1]["result"]

    @pytest.mark.asyncio
    async def test_log_error_delegates(self):
        mock_activity = MagicMock(spec=ActivityLogger)
        logger = ActivityToolAuditLogger(mock_activity)

        await logger.log_error("timeout", turn_count=2, duration_ms=3000)

        mock_activity.error.assert_called_once()
        call_kwargs = mock_activity.error.call_args
        assert call_kwargs[0][0] == "agent_error"
        assert call_kwargs[1]["error"] == "timeout"
        assert call_kwargs[1]["turn_count"] == 2
        assert call_kwargs[1]["duration_ms"] == 3000

    @pytest.mark.asyncio
    async def test_log_tool_start_truncates_long_params(self):
        mock_activity = MagicMock(spec=ActivityLogger)
        logger = ActivityToolAuditLogger(mock_activity)

        long_params = {"data": "x" * 1000}
        await logger.log_tool_start("write", long_params)

        call_kwargs = mock_activity.info.call_args
        assert len(call_kwargs[1]["parameters"]) <= 500