"""Tests for tool_audit_logger module."""

import pytest

from shannon_core.agents.tool_audit_logger import (
    NullToolAuditLogger,
    ToolAuditLogger,
)


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