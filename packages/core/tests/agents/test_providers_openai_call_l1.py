"""L1 call() 集成：validate_json 抛 StructuredOutputParseError 时触发轻量重输。

堵 task-probe 盲区（task probe 只覆盖无 output_type 的子代理，覆盖不到带 structured
output 的顶层 agent）。用 monkeypatch 让 Runner.run_streamed 的 stream_events 抛
StructuredOutputParseError，验证 call() 调 _lightweight_reparse。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from agents import MaxTurnsExceeded

from supernova_core.agents.openai_output_schema import StructuredOutputParseError
from supernova_core.agents.providers_openai import OpenAIProvider, _ReparsedRunResult
from supernova_core.agents.runner import ProviderConfig


def _provider():
    return OpenAIProvider(ProviderConfig(
        type="openai_compatible", api_key="test", base_url="https://x.example.com"))


def _streaming_result_that_raises(exc):
    """伪造 Runner.run_streamed 返回的对象：stream_events 抛 exc。"""
    result = MagicMock()

    async def _stream():
        if False:  # 保证是 async generator
            yield
        raise exc

    result.stream_events = _stream
    return result


def _usage(inp, outp, cached=0):
    u = MagicMock()
    u.input_tokens = inp
    u.output_tokens = outp
    u.input_tokens_details = MagicMock(cached_tokens=cached) if cached else None
    return u


def _streaming_result_with_usage(exc, *, inp, outp):
    """run_streamed 返回对象：stream_events 抛 exc，但 context_wrapper.usage 已累积
    （模拟 SDK 行为：每个 response 后 add，异常/MaxTurnsExceeded 时已完成部分保留）。"""
    result = MagicMock()
    result.context_wrapper = MagicMock()
    result.context_wrapper.usage = _usage(inp, outp)
    result.final_output = ""

    async def _stream():
        if False:  # 保证是 async generator
            yield
        raise exc

    result.stream_events = _stream
    return result


@pytest.mark.asyncio
async def test_call_l1_recovers_structured_output(monkeypatch):
    p = _provider()
    monkeypatch.setattr(p, "_lightweight_reparse", AsyncMock(return_value=_ReparsedRunResult(
        {"vulnerabilities": []}, input_tokens=3, output_tokens=7)))
    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run_streamed",
        MagicMock(return_value=_streaming_result_that_raises(StructuredOutputParseError("bad"))))

    result = await p.call(prompt="P", cwd="/tmp", model_tier="medium",
                          output_format={"type": "object"})
    assert result.success is True
    assert result.structured_output == {"vulnerabilities": []}
    assert result.tokens.input_tokens == 3
    assert result.tokens.output_tokens == 7


@pytest.mark.asyncio
async def test_call_l1_failure_raises_for_l2(monkeypatch):
    """L1 也失败 → re-raise StructuredOutputParseError → 外层 _handle_error → L2。"""
    p = _provider()
    monkeypatch.setattr(p, "_lightweight_reparse", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run_streamed",
        MagicMock(return_value=_streaming_result_that_raises(StructuredOutputParseError("bad"))))

    result = await p.call(prompt="P", cwd="/tmp", model_tier="medium",
                          output_format={"type": "object"})
    assert result.success is False
    from supernova_core.models.errors import ErrorCode
    assert result.error_code == ErrorCode.OUTPUT_VALIDATION_FAILED


@pytest.mark.asyncio
async def test_call_max_turns_records_accumulated_cost(monkeypatch):
    """L1: MaxTurnsExceeded 时 result.context_wrapper.usage 已累积到超轮数（SDK 无清零），
    call() 据此算 cost，而非 _MaxTurnsStub 硬编码 0（修 error path cost 归 0）。"""
    p = _provider()
    monkeypatch.setattr(p, "_get_model", lambda tier: "glm-5.2")
    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run_streamed",
        MagicMock(return_value=_streaming_result_with_usage(
            MaxTurnsExceeded("max turns"), inp=1000, outp=500)))
    result = await p.call(prompt="P", cwd="/tmp", model_tier="medium")
    assert result.stop_reason == "max_turns"
    assert result.success is False
    assert result.cost > 0  # 1000 in + 500 out × glm-5.2 价 → 非 0


@pytest.mark.asyncio
async def test_call_stream_error_records_accumulated_cost(monkeypatch):
    """L1: stream 消费中途异常（如 stall）→ 外层 _handle_error，从已累积的 usage 算 cost
    （run_result 经参数传入），而非硬编码 0（修 error path cost 归 0）。"""
    p = _provider()
    monkeypatch.setattr(p, "_get_model", lambda tier: "glm-5.2")
    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run_streamed",
        MagicMock(return_value=_streaming_result_with_usage(
            RuntimeError("stream stall"), inp=800, outp=400)))
    result = await p.call(prompt="P", cwd="/tmp", model_tier="medium")
    assert result.success is False
    assert result.cost > 0  # 异常前已累积 usage → 非 0
