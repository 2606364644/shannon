import pytest
from unittest.mock import AsyncMock

from supernova_core.audit.session import AuditSession
from supernova_core.audit.session_registry import NullAuditSession


@pytest.mark.asyncio
async def test_audit_session_log_info_routes_to_workflow_logger():
    session = AuditSession.__new__(AuditSession)
    session._workflow_logger = AsyncMock()
    await session.log_info("msg", level="warning")
    session._workflow_logger.log_info.assert_awaited_once_with("msg", level="warning")


@pytest.mark.asyncio
async def test_audit_session_log_info_defaults_info():
    session = AuditSession.__new__(AuditSession)
    session._workflow_logger = AsyncMock()
    await session.log_info("msg")
    session._workflow_logger.log_info.assert_awaited_once_with("msg", level="info")


@pytest.mark.asyncio
async def test_audit_session_log_info_noop_without_logger():
    session = AuditSession.__new__(AuditSession)
    session._workflow_logger = None
    await session.log_info("msg")  # 不应 raise


@pytest.mark.asyncio
async def test_null_session_log_info_is_noop():
    await NullAuditSession().log_info("x", level="warning")  # 不应 raise
