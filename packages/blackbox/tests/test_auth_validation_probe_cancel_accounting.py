"""run_auth_validation_probe cancel 兜底记账（2026-08-28 authcheck 超时丢账修复）。

Temporal start_to_close_timeout 到期 → activity 协程收 CancelledError（BaseException）
→ 原 except Exception 接不住 → end_agent 永不执行 → AgentEvent end 缺失、usage 丢
（NodeGoat-20260827-152204 现场：3 次 attempt 全超时，3 个 start 0 个 end，账 0）。

修复语义：except BaseException 分支从 executor.usage_sink（provider cancel 分支已写）
取已花值构造 AgentEndResult → shield(end_agent) 落账 → 原样 re-raise（cancel/重试
语义不变）。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from supernova_core.agents.runner import UsageSink
from supernova_blackbox.pipeline.activities import run_auth_validation_probe
from supernova_blackbox.pipeline.shared import BlackboxActivityInput


def _input():
    return BlackboxActivityInput(
        web_url="http://target/", config_path="/c.yaml", workspace_path="/wp"
    )


class _FakeSession:
    """最小 AuditSession stand-in：只记 start_agent/end_agent 调用。"""

    def __init__(self):
        self.started = None
        self.ended = None

    async def start_agent(self, name, desc, attempt=None):
        self.started = (name, attempt)

    async def end_agent(self, name, result):
        self.ended = (name, result)

    async def log_tool_call(self, *a, **k): pass
    async def log_llm_turn(self, *a, **k): pass
    async def log_error(self, *a, **k): pass


def _shell(monkeypatch, session):
    """屏蔽 probe 的 session 注册 / attempt 上下文，返回 fake session。"""
    monkeypatch.setattr(
        "supernova_blackbox.pipeline.activities.activity.info",
        lambda: SimpleNamespace(attempt=2, workflow_id="wf-x"))

    async def _noop_ensure(inp):
        return None

    monkeypatch.setattr(
        "supernova_blackbox.pipeline.activities.ensure_audit_session", _noop_ensure)
    monkeypatch.setattr(
        "supernova_core.audit.session_registry.get_audit_session",
        lambda: session)
    return session


@pytest.mark.asyncio
async def test_probe_cancelled_records_partial_usage(monkeypatch):
    """cancel：sink 已花值 → end_agent 记账（AgentEndResult 带 cost/tokens/attempt）→ re-raise。"""
    session = _shell(monkeypatch, _FakeSession())

    async def cancelled_validate(**kw):
        exe = kw["executor"]
        # 模拟 provider cancel 分支已写入的部分消耗（executor.usage_sink 通道）
        exe.usage_sink = UsageSink(
            model="deepseek-v4-flash", input_tokens=500, output_tokens=200,
            cache_read_tokens=500, cache_creation_tokens=0,
            cost_usd=0.00091, cost_currency="CNY")
        raise asyncio.CancelledError()

    with patch("supernova_blackbox.pipeline.activities.validate_authentication",
               new=cancelled_validate):
        with pytest.raises(asyncio.CancelledError):
            await run_auth_validation_probe(_input())

    assert session.ended is not None, "cancel 必须落 end_agent（修复前直接穿透，end 丢失）"
    name, result = session.ended
    assert name == "validate-authentication"
    assert result.success is False
    assert result.cost_usd == pytest.approx(0.00091)
    assert result.cost_currency == "CNY"
    assert result.input_tokens == 500
    assert result.output_tokens == 200
    assert result.cache_read_tokens == 500
    assert result.model == "deepseek-v4-flash"
    assert result.attempt_number == 2
    assert "cancel" in (result.error or "").lower() or "timeout" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_probe_cancelled_without_sink_still_records_end(monkeypatch):
    """cancel 且 sink 无值（引擎拿不到中途消耗）：end_agent 仍被调（cost 0、error 注明）。"""
    session = _shell(monkeypatch, _FakeSession())

    async def cancelled_validate(**kw):
        raise asyncio.CancelledError()

    with patch("supernova_blackbox.pipeline.activities.validate_authentication",
               new=cancelled_validate):
        with pytest.raises(asyncio.CancelledError):
            await run_auth_validation_probe(_input())

    assert session.ended is not None
    name, result = session.ended
    assert name == "validate-authentication"
    assert result.success is False
    assert result.cost_usd == 0.0
    assert "cancel" in (result.error or "").lower() or "timeout" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_probe_normal_exception_path_unchanged(monkeypatch):
    """普通 Exception 降级路径不受影响（仍吞异常返回 success=False，不 re-raise）。"""
    session = _shell(monkeypatch, _FakeSession())

    async def failing_validate(**kw):
        raise RuntimeError("engine down")

    with patch("supernova_blackbox.pipeline.activities.validate_authentication",
               new=failing_validate):
        result = await run_auth_validation_probe(_input())

    assert result.success is False  # 降级返回，不抛
    assert session.ended is not None
    assert session.ended[1].success is False
