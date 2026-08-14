"""t2 run_blackbox_auth_validation 可观测性：agent 收尾必须反映真实 verdict。

2026-08-14 发现：validate_authentication 正常返回 success=False 时，activity 先
close/end_agent(success=True) 再 raise PentestError 进 except 又 close/end_agent
(success=False)——end_agent 双记且首条失真（黑盒 auth 失败时 events 里 agent
显示成功）。锁定：validate 正常返回路径 end_agent 恰一次且 success=verdict。
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from supernova_core.services.validate_authentication import AuthValidationResult
from supernova_blackbox.pipeline.activities import run_blackbox_auth_validation
from supernova_blackbox.pipeline.shared import BlackboxActivityInput


def _wire(monkeypatch, session, logger):
    monkeypatch.setattr(
        "supernova_blackbox.pipeline.activities.activity.info",
        lambda: SimpleNamespace(attempt=1, workflow_id="w"))
    monkeypatch.setattr(
        "supernova_blackbox.pipeline.activities.ensure_audit_session", AsyncMock())
    monkeypatch.setattr(
        "supernova_core.audit.session_registry.get_audit_session", lambda: session)
    monkeypatch.setattr(
        "supernova_core.audit.session_tool_audit_logger.SessionToolAuditLogger",
        MagicMock(return_value=logger))


def _session():
    session = MagicMock()
    session.start_agent = AsyncMock()
    session.end_agent = AsyncMock()
    session.log_error = AsyncMock()
    return session


def _logger():
    logger = MagicMock()
    logger.initialize = AsyncMock()
    logger.close = AsyncMock()
    return logger


@pytest.mark.asyncio
async def test_failed_verdict_records_agent_end_once_with_false(monkeypatch, tmp_path):
    """validate_authentication 返回 success=False → end_agent 恰一次且 success=False
    + ApplicationFailure fail-fast（不再先记 success=True 再双记）。"""
    from temporalio.exceptions import ApplicationError as ApplicationFailure

    session = _session()
    _wire(monkeypatch, session, _logger())

    with patch(
        "supernova_core.services.validate_authentication.validate_authentication",
        new=AsyncMock(return_value=AuthValidationResult(
            success=False, failure_point="username_or_password")),
    ):
        with pytest.raises(ApplicationFailure):
            await run_blackbox_auth_validation(BlackboxActivityInput(
                web_url="http://t/", config_path="/c.yaml",
                workspace_path=str(tmp_path), repo_path=""))

    assert session.end_agent.await_count == 1, (
        "end_agent 必须恰记录一次——双记（success=True + from_pentest_error）"
        "会在 events 产生矛盾的 AgentEvent end")
    assert session.end_agent.await_args.args[1].success is False


@pytest.mark.asyncio
async def test_success_verdict_records_agent_end_with_true(monkeypatch, tmp_path):
    """validate_authentication 返回 success=True → end_agent 恰一次且 success=True（回归）。"""
    session = _session()
    _wire(monkeypatch, session, _logger())

    with patch(
        "supernova_core.services.validate_authentication.validate_authentication",
        new=AsyncMock(return_value=AuthValidationResult(success=True)),
    ):
        await run_blackbox_auth_validation(BlackboxActivityInput(
            web_url="http://t/", config_path="/c.yaml",
            workspace_path=str(tmp_path), repo_path=""))

    assert session.end_agent.await_count == 1
    assert session.end_agent.await_args.args[1].success is True
