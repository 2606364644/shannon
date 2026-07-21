"""L1 call() 集成：validate_json 抛 StructuredOutputParseError 时触发轻量重输。

堵 task-probe 盲区（task probe 只覆盖无 output_type 的子代理，覆盖不到带 structured
output 的顶层 agent）。用 monkeypatch 让 Runner.run_streamed 的 stream_events 抛
StructuredOutputParseError，验证 call() 调 _lightweight_reparse。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

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
