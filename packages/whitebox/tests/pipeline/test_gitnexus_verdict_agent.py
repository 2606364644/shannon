import pytest
from unittest.mock import AsyncMock, MagicMock
from shannon_whitebox.pipeline import activities


@pytest.mark.asyncio
async def test_verdict_agent_reads_max_turns_env(monkeypatch):
    """SHANNON_GITNEXUS_VERDICT_MAX_TURNS env 透传给 run_claude_prompt。"""
    monkeypatch.setenv("SHANNON_GITNEXUS_VERDICT_MAX_TURNS", "7")
    captured: dict = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        result = MagicMock()
        result.text = "ok"
        result.success = True
        result.turns = 1
        return result

    # 延迟 import 从源模块取，patch 源模块有效
    monkeypatch.setattr("shannon_core.agents.runner.run_claude_prompt", fake_run)

    await activities.run_gitnexus_verdict_agent(prompt="p", repo_path="/r")

    assert captured["max_turns"] == 7
    assert captured["model_tier"] == "medium"


@pytest.mark.asyncio
async def test_verdict_agent_default_max_turns(monkeypatch):
    """不设 env 时默认 30。"""
    monkeypatch.delenv("SHANNON_GITNEXUS_VERDICT_MAX_TURNS", raising=False)
    captured: dict = {}
    async def fake_run(**kwargs):
        captured.update(kwargs)
        return MagicMock(text="ok", success=True, turns=1)
    monkeypatch.setattr("shannon_core.agents.runner.run_claude_prompt", fake_run)

    await activities.run_gitnexus_verdict_agent(prompt="p", repo_path="/r")
    assert captured["max_turns"] == 30


@pytest.mark.asyncio
async def test_verdict_agent_attaches_tool_audit_logger(monkeypatch):
    """传 audit_session 时构造 SessionToolAuditLogger 并传给 run_claude_prompt。"""
    captured: dict = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return MagicMock(text="ok", success=True, turns=1)

    monkeypatch.setattr("shannon_core.agents.runner.run_claude_prompt", fake_run)

    logger_instance = MagicMock()
    logger_instance.initialize = AsyncMock()
    logger_instance.close = AsyncMock()

    def fake_logger_cls(session, name, attempt):
        assert name == "gitnexus-verdict"
        return logger_instance

    # patch 源模块（verdict_agent 内延迟 import 该模块）
    monkeypatch.setattr(
        "shannon_whitebox.audit.session_tool_audit_logger.SessionToolAuditLogger",
        fake_logger_cls,
    )

    await activities.run_gitnexus_verdict_agent(
        prompt="p", repo_path="/r", audit_session=MagicMock()
    )

    assert captured.get("tool_audit_logger") is logger_instance
    logger_instance.initialize.assert_awaited_once()
    logger_instance.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_verdict_agent_no_audit_session_no_logger(monkeypatch):
    """audit_session=None 时不构造 logger（tool_audit_logger=None，向后兼容）。"""
    captured: dict = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return MagicMock(text="ok", success=True, turns=1)

    monkeypatch.setattr("shannon_core.agents.runner.run_claude_prompt", fake_run)

    # 若误构造 logger，此 patch 会让构造抛错
    def fake_logger_cls(session, name, attempt):
        raise AssertionError("audit_session=None 时不应构造 SessionToolAuditLogger")

    monkeypatch.setattr(
        "shannon_whitebox.audit.session_tool_audit_logger.SessionToolAuditLogger",
        fake_logger_cls,
    )

    await activities.run_gitnexus_verdict_agent(prompt="p", repo_path="/r")

    assert captured.get("tool_audit_logger") is None
