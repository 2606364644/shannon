import pytest
from unittest.mock import AsyncMock, MagicMock
from supernova_core.agents.runner import ClaudeRunResult, TokenUsage
from supernova_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
from supernova_whitebox.pipeline import activities


def _patch_verdict_llm(monkeypatch, fake_result) -> dict:
    """公共 mock：patch run_claude_prompt 返回固定 result + SessionToolAuditLogger
    为 no-op MagicMock（记账测试不关心审计文件写入）。"""
    async def fake_run(**kwargs):
        return fake_result

    monkeypatch.setattr("supernova_core.agents.runner.run_claude_prompt", fake_run)

    logger_instance = MagicMock(spec=SessionToolAuditLogger)
    logger_instance.initialize = AsyncMock()
    logger_instance.close = AsyncMock()
    monkeypatch.setattr(
        "supernova_whitebox.audit.session_tool_audit_logger.SessionToolAuditLogger",
        lambda session, name, attempt: logger_instance,
    )
    return logger_instance


@pytest.mark.asyncio
async def test_verdict_agent_reads_max_turns_env(monkeypatch):
    """SUPERNOVA_GITNEXUS_VERDICT_MAX_TURNS env 透传给 run_claude_prompt。"""
    monkeypatch.setenv("SUPERNOVA_GITNEXUS_VERDICT_MAX_TURNS", "7")
    captured: dict = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        result = MagicMock()
        result.text = "ok"
        result.success = True
        result.turns = 1
        return result

    # 延迟 import 从源模块取，patch 源模块有效
    monkeypatch.setattr("supernova_core.agents.runner.run_claude_prompt", fake_run)

    await activities.run_gitnexus_verdict_agent(prompt="p", repo_path="/r")

    assert captured["max_turns"] == 7
    assert captured["model_tier"] == "medium"


@pytest.mark.asyncio
async def test_verdict_agent_default_max_turns(monkeypatch):
    """不设 env 时默认 30。"""
    monkeypatch.delenv("SUPERNOVA_GITNEXUS_VERDICT_MAX_TURNS", raising=False)
    captured: dict = {}
    async def fake_run(**kwargs):
        captured.update(kwargs)
        return MagicMock(text="ok", success=True, turns=1)
    monkeypatch.setattr("supernova_core.agents.runner.run_claude_prompt", fake_run)

    await activities.run_gitnexus_verdict_agent(prompt="p", repo_path="/r")
    assert captured["max_turns"] == 30


@pytest.mark.asyncio
async def test_verdict_agent_attaches_tool_audit_logger(monkeypatch):
    """传 audit_session 时构造 SessionToolAuditLogger 并传给 run_claude_prompt。"""
    captured: dict = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return ClaudeRunResult(text="ok", success=True, turns=1)

    monkeypatch.setattr("supernova_core.agents.runner.run_claude_prompt", fake_run)

    # spec=SessionToolAuditLogger 强制真实签名：close 缺 duration_ms 会抛 TypeError，
    # 防止生产代码再次漏传该必填参数（regression guard）。
    logger_instance = MagicMock(spec=SessionToolAuditLogger)
    logger_instance.initialize = AsyncMock()
    logger_instance.close = AsyncMock()

    def fake_logger_cls(session, name, attempt):
        assert name == "gitnexus-verdict"
        return logger_instance

    # patch 源模块（verdict_agent 内延迟 import 该模块）
    monkeypatch.setattr(
        "supernova_whitebox.audit.session_tool_audit_logger.SessionToolAuditLogger",
        fake_logger_cls,
    )

    audit_session = MagicMock()
    audit_session.end_agent = AsyncMock()  # 对齐 AuditSession.end_agent 真实 async 签名
    await activities.run_gitnexus_verdict_agent(
        prompt="p", repo_path="/r", audit_session=audit_session
    )

    assert captured.get("tool_audit_logger") is logger_instance
    logger_instance.initialize.assert_awaited_once()
    # close 必须带 success + duration_ms（int），对齐 SessionToolAuditLogger.close 真实签名。
    logger_instance.close.assert_awaited_once()
    close_kwargs = logger_instance.close.await_args.kwargs
    assert close_kwargs["success"] is True
    assert isinstance(close_kwargs["duration_ms"], int)


@pytest.mark.asyncio
async def test_verdict_agent_no_audit_session_no_logger(monkeypatch):
    """audit_session=None 时不构造 logger（tool_audit_logger=None，向后兼容）。"""
    captured: dict = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return MagicMock(text="ok", success=True, turns=1)

    monkeypatch.setattr("supernova_core.agents.runner.run_claude_prompt", fake_run)

    # 若误构造 logger，此 patch 会让构造抛错
    def fake_logger_cls(session, name, attempt):
        raise AssertionError("audit_session=None 时不应构造 SessionToolAuditLogger")

    monkeypatch.setattr(
        "supernova_whitebox.audit.session_tool_audit_logger.SessionToolAuditLogger",
        fake_logger_cls,
    )

    await activities.run_gitnexus_verdict_agent(prompt="p", repo_path="/r")

    assert captured.get("tool_audit_logger") is None


@pytest.mark.asyncio
async def test_verdict_agent_records_cost_into_metrics(monkeypatch):
    """成功路径：result 的 cost/tokens/model 经 audit_session.end_agent 记入
    metrics（修 authz 深判成本漏记——2026-08-25 NodeGoat 扫描实证 gitnexus-verdict
    7 轮 LLM 的 result.cost 被调用方丢弃，session.json metrics 无此条目）。"""
    fake_result = ClaudeRunResult(
        text="ok", success=True, duration=47835, turns=7,
        cost=0.25, cost_currency="CNY", model="deepseek-v4-pro",
        tokens=TokenUsage(input_tokens=100, output_tokens=200,
                          cache_read_input_tokens=300,
                          cache_creation_input_tokens=0),
    )
    _patch_verdict_llm(monkeypatch, fake_result)

    audit_session = MagicMock()
    audit_session.end_agent = AsyncMock()

    await activities.run_gitnexus_verdict_agent(
        prompt="p", repo_path="/r", audit_session=audit_session)

    audit_session.end_agent.assert_awaited_once()
    pos_args = audit_session.end_agent.await_args.args
    assert pos_args[0] == "gitnexus-verdict"
    end_result = pos_args[1]
    assert end_result.success is True
    assert end_result.cost_usd == pytest.approx(0.25)
    assert end_result.cost_currency == "CNY"
    assert end_result.model == "deepseek-v4-pro"
    assert end_result.num_turns == 7
    assert end_result.input_tokens == 100
    assert end_result.output_tokens == 200
    assert end_result.cache_read_tokens == 300
    assert end_result.cache_creation_tokens == 0
    assert isinstance(end_result.duration_ms, int)


@pytest.mark.asyncio
async def test_verdict_agent_records_cost_on_failure(monkeypatch):
    """失败路径同样记账（对齐 agent runner 立场：失败 agent 也记真实消耗）。"""
    fake_result = ClaudeRunResult(
        text="", success=False, turns=3,
        cost=0.02, cost_currency="CNY", model="deepseek-v4-pro",
        error="boom",
        tokens=TokenUsage(input_tokens=10, output_tokens=0),
    )
    _patch_verdict_llm(monkeypatch, fake_result)

    audit_session = MagicMock()
    audit_session.end_agent = AsyncMock()

    await activities.run_gitnexus_verdict_agent(
        prompt="p", repo_path="/r", audit_session=audit_session)

    audit_session.end_agent.assert_awaited_once()
    end_result = audit_session.end_agent.await_args.args[1]
    assert end_result.success is False
    assert end_result.cost_usd == pytest.approx(0.02)
    assert end_result.error == "boom"


@pytest.mark.asyncio
async def test_verdict_agent_agent_name_passthrough(monkeypatch):
    """agent_name 参数透传 end_agent——多次调用场景（gn 富化逐 class、endpoint
    富化逐 queue、回炉）防 metrics.agents 同名条目互相覆盖。"""
    _patch_verdict_llm(
        monkeypatch,
        ClaudeRunResult(text="ok", success=True, turns=1, cost=0.01,
                        cost_currency="CNY", model="m"),
    )

    audit_session = MagicMock()
    audit_session.end_agent = AsyncMock()

    await activities.run_gitnexus_verdict_agent(
        prompt="p", repo_path="/r", audit_session=audit_session,
        agent_name="gn-enrich-xss")

    assert audit_session.end_agent.await_args.args[0] == "gn-enrich-xss"


@pytest.mark.asyncio
async def test_verdict_agent_no_audit_session_no_metrics(monkeypatch):
    """audit_session=None 不记账（CLI 直跑无 metrics，行为同前）。"""
    _patch_verdict_llm(
        monkeypatch,
        ClaudeRunResult(text="ok", success=True, turns=1),
    )
    # 不传 audit_session：若误记账会在 None 上炸 AttributeError
    result = await activities.run_gitnexus_verdict_agent(prompt="p", repo_path="/r")
    assert result.success is True


def test_gitnexus_verdict_phase_mapping():
    """gitnexus-verdict 归 vulnerability-analysis 相——phase 汇总含深判成本
    （authz-gitnexus-judge step 跑在该相）。"""
    from supernova_core.models.agents import AGENT_PHASE_MAP
    assert AGENT_PHASE_MAP.get("gitnexus-verdict") == "vulnerability-analysis"
