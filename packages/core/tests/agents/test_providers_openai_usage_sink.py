"""providers_openai cancel 兜底：被 cancel 前把 context_wrapper.usage 已花值写 usage_sink。

2026-08-28 authcheck 超时丢账修复的 provider 侧：Temporal start_to_close_timeout
cancel 掉 activity → CancelledError 穿透 except Exception（BaseException 分支）→
正常返回路径的 usage 拿不到。openai 引擎 SDK 的 RunResultStreaming.context_wrapper
.usage 每 turn 累积（无清零），cancel 分支从这里取已花值 → compute_cost → 写
usage_sink → re-raise（cancel 语义不变，Temporal 照常重试/超时收口）。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from supernova_core.agents.providers_openai import OpenAIProvider
from supernova_core.agents.runner import ProviderConfig, UsageSink


def _provider():
    return OpenAIProvider(ProviderConfig(
        type="openai_compatible", api_key="test",
        base_url="https://x.example.com", model="deepseek-v4-flash"))


@pytest.mark.asyncio
async def test_call_cancelled_writes_sink_and_reraises(monkeypatch):
    """stream 消费中被 cancel：sink 收已花值（归一 cache）+ CancelledError 原样上抛。"""
    p = _provider()
    sink = UsageSink()

    # SDK 累积形态：input_tokens 含 cache 命中，input_tokens_details.cached_tokens 标命中量
    usage = SimpleNamespace(
        input_tokens=1000, output_tokens=200,
        input_tokens_details=SimpleNamespace(cached_tokens=500))
    result = MagicMock()
    result.context_wrapper = SimpleNamespace(usage=usage)

    async def _cancelled_stream():
        yield MagicMock(type="agent_updated_stream_event")
        raise asyncio.CancelledError()

    result.stream_events = _cancelled_stream
    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run_streamed",
        MagicMock(return_value=result))

    with pytest.raises(asyncio.CancelledError):
        await p.call(prompt="P", cwd="/tmp", model_tier="medium", usage_sink=sink)

    # 已花值落 sink：input 归一为不含 cache 命中（500），cache_read 单列
    assert sink.input_tokens == 500
    assert sink.cache_read_tokens == 500
    assert sink.output_tokens == 200
    assert sink.cache_creation_tokens == 0
    # deepseek-v4-flash 价（in ¥1/M、out ¥2/M、cache_read ¥0.2/M，2026-08-31 更新）：
    # (500×1 + 500×0.2 + 200×2)/1e6 = 1000.0/1e6 → 0.001
    assert sink.cost_usd == pytest.approx(0.001)
    assert sink.cost_currency == "CNY"
    assert sink.model == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_call_cancelled_before_stream_no_sink_write(monkeypatch):
    """run_streamed 返回前就 cancel（streaming=None）：不写 sink、不炸、原样上抛。"""
    p = _provider()
    sink = UsageSink()

    def _blow_up(*a, **kw):
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run_streamed", _blow_up)

    with pytest.raises(asyncio.CancelledError):
        await p.call(prompt="P", cwd="/tmp", model_tier="medium", usage_sink=sink)

    assert sink.input_tokens == 0
    assert sink.cost_usd == 0.0


@pytest.mark.asyncio
async def test_call_cancelled_without_sink_still_reraises(monkeypatch):
    """不传 sink（旧调用方/其余 agent 路径）：行为与修复前一致，仅 cancel 上抛。"""
    p = _provider()

    usage = SimpleNamespace(
        input_tokens=1000, output_tokens=200,
        input_tokens_details=SimpleNamespace(cached_tokens=500))
    result = MagicMock()
    result.context_wrapper = SimpleNamespace(usage=usage)

    async def _cancelled_stream():
        yield MagicMock(type="agent_updated_stream_event")
        raise asyncio.CancelledError()

    result.stream_events = _cancelled_stream
    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run_streamed",
        MagicMock(return_value=result))

    with pytest.raises(asyncio.CancelledError):
        await p.call(prompt="P", cwd="/tmp", model_tier="medium")
