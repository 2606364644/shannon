import pytest
from unittest.mock import AsyncMock, MagicMock

from shannon_whitebox.audit.audit_logger import (
    AuditLogger,
    RealAuditLogger,
    NullAuditLogger,
    create_audit_logger,
)


def test_create_audit_logger_returns_null_when_session_is_none():
    logger = create_audit_logger(None)
    assert isinstance(logger, NullAuditLogger)


def test_create_audit_logger_returns_real_when_session_provided():
    session = MagicMock()
    logger = create_audit_logger(session)
    assert isinstance(logger, RealAuditLogger)


@pytest.mark.asyncio
async def test_null_logger_log_llm_response():
    logger = NullAuditLogger()
    # Should not raise
    await logger.log_llm_response(turn=1, content="test")


@pytest.mark.asyncio
async def test_null_logger_log_tool_start():
    logger = NullAuditLogger()
    await logger.log_tool_start("Bash", {"command": "ls"})


@pytest.mark.asyncio
async def test_null_logger_log_tool_end():
    logger = NullAuditLogger()
    await logger.log_tool_end({"exit_code": 0})


@pytest.mark.asyncio
async def test_null_logger_log_error():
    logger = NullAuditLogger()
    await logger.log_error(RuntimeError("boom"), duration=5000, turns=3)


@pytest.mark.asyncio
async def test_real_logger_log_llm_response():
    session = AsyncMock()
    logger = RealAuditLogger(session)
    await logger.log_llm_response(turn=2, content="hello")
    session.log_event.assert_called_once_with("llm_response", {"turn": 2, "content": "hello"})


@pytest.mark.asyncio
async def test_real_logger_log_tool_start():
    session = AsyncMock()
    logger = RealAuditLogger(session)
    await logger.log_tool_start("Read", {"file_path": "/tmp/x"})
    session.log_event.assert_called_once_with("tool_start", {"toolName": "Read", "parameters": {"file_path": "/tmp/x"}})


@pytest.mark.asyncio
async def test_real_logger_log_tool_end():
    session = AsyncMock()
    logger = RealAuditLogger(session)
    await logger.log_tool_end({"result": "ok"})
    session.log_event.assert_called_once_with("tool_end", {"result": "ok"})


@pytest.mark.asyncio
async def test_real_logger_log_error():
    session = AsyncMock()
    logger = RealAuditLogger(session)
    err = ValueError("bad value")
    await logger.log_error(err, duration=3000, turns=5)
    session.log_event.assert_called_once_with("error", {
        "message": "bad value",
        "errorType": "ValueError",
        "duration": 3000,
        "turns": 5,
    })
