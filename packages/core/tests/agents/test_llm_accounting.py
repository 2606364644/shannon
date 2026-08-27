"""轻量单次 LLM client 记账（spec 2026-08-27 §8）。

2026-08-27 计费检查发现：轻量单次调用（track-parity / poc gap-fill /
expected_response / report summary / recon summary）cost 系统性漏记——
client 闭包把 ClaudeRunResult 剥成 str，cost/tokens 无归宿。本模块提取
22269e4a（run_gitnexus_verdict_agent 记账）的通用模式：包装 runner →
累计 → finalize 一次 end_agent。

契约：runner = async (prompt, **kw) -> ClaudeRunResult-like（成功/失败都返回，
异常向上抛由本层吞为 None）；client(prompt, **kw) -> str | None（structured_output
优先 JSON 化，回落 text；success=False / 异常 → None，调用方降级）。
"""
import pytest

from supernova_core.agents.llm_accounting import AccountedLlmClient
from supernova_core.agents.runner import TokenUsage


class _Result:
    def __init__(self, cost=0.01, turns=1, success=True, so=None, text="raw"):
        self.success = success
        self.cost = cost
        self.cost_currency = "CNY"
        self.model = "glm-5.3"
        self.turns = turns
        self.tokens = TokenUsage(input_tokens=100, output_tokens=50,
                                 cache_read_input_tokens=0,
                                 cache_creation_input_tokens=0)
        self.structured_output = so
        self.text = text


class _RecordingSession:
    def __init__(self):
        self.agents = []

    async def end_agent(self, name, result):
        self.agents.append((name, result))


@pytest.mark.asyncio
async def test_accumulates_and_records_once():
    """N 次调用累计 cost/tokens，finalize 一次 end_agent 记总账。"""
    session = _RecordingSession()

    async def runner(prompt, **kw):
        return _Result(cost=0.02)

    client = AccountedLlmClient(runner, session, "track-parity")
    assert await client("p1") == "raw"
    assert await client("p2") == "raw"
    assert session.agents == []          # 未 finalize 不记账
    await client.finalize()
    assert len(session.agents) == 1      # 一次总账，非 per-call 刷
    name, res = session.agents[0]
    assert name == "track-parity"
    assert res.cost_usd == pytest.approx(0.04)
    assert res.cost_currency == "CNY"
    assert res.input_tokens == 200
    assert res.output_tokens == 100
    assert res.num_turns == 2
    assert res.success is True


@pytest.mark.asyncio
async def test_structured_output_preferred():
    async def runner(prompt, **kw):
        return _Result(so={"items": [1]}, text="fallback")

    client = AccountedLlmClient(runner, _RecordingSession(), "x")
    out = await client("p")
    import json
    assert json.loads(out) == {"items": [1]}


@pytest.mark.asyncio
async def test_failed_call_returns_none_and_success_reflects():
    session = _RecordingSession()

    async def runner(prompt, **kw):
        if prompt == "bad":
            return _Result(success=False, cost=0.0, text="")
        return _Result(cost=0.01)

    client = AccountedLlmClient(runner, session, "poc-gapfill")
    assert await client("good") == "raw"
    assert await client("bad") is None
    await client.finalize()
    _, res = session.agents[0]
    assert res.cost_usd == pytest.approx(0.01)
    assert res.success is False   # 有失败调用 → 总账 success=False（诊断可见）


@pytest.mark.asyncio
async def test_exception_swallowed_to_none():
    async def runner(prompt, **kw):
        raise RuntimeError("llm down")

    session = _RecordingSession()
    client = AccountedLlmClient(runner, session, "recon-summary")
    assert await client("p") is None
    await client.finalize()
    assert session.agents == []  # 零成功调用零失败记账 → 不产生幽灵条目


@pytest.mark.asyncio
async def test_no_calls_and_no_session_noop():
    async def runner(prompt, **kw):
        raise AssertionError("should not be called")

    client = AccountedLlmClient(runner, None, "report-summary")
    await client.finalize()  # no session / no calls → no-op，不 crash


@pytest.mark.asyncio
async def test_call_result_returns_result_and_records():
    """call_result：返回原始 result（调用方需要 success/structured_output
    语义做 retry 判定，如 poc gap-fill），同规则记账。"""
    session = _RecordingSession()

    async def runner(prompt, **kw):
        return _Result(cost=0.05)

    client = AccountedLlmClient(runner, session, "poc-gapfill")
    res = await client.call_result("p")
    assert res.success is True
    assert res.structured_output is None
    await client.finalize()
    _, rec = session.agents[0]
    assert rec.cost_usd == pytest.approx(0.05)
