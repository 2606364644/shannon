"""L1 provider 轻量重输：L0 容错失败后发单个 chat completion 让 GLM 把分析转纯 JSON。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from supernova_core.agents.providers_openai import OpenAIProvider, _ReparsedRunResult
from supernova_core.agents.runner import ProviderConfig


def _provider_with_client(fake_client):
    p = OpenAIProvider(ProviderConfig(
        type="openai_compatible", api_key="test", base_url="https://x.example.com"))
    p._client = fake_client
    return p


def _fake_chat_response(content, prompt_tokens=5, completion_tokens=10):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return resp


@pytest.mark.asyncio
async def test_lightweight_reparse_recovers_pure_json():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_chat_response('{"vulnerabilities": []}'))
    p = _provider_with_client(client)
    out = await p._lightweight_reparse("some analysis text", {"type": "object"}, "m")
    assert isinstance(out, _ReparsedRunResult)
    assert out.final_output == {"vulnerabilities": []}
    assert out.context_wrapper.usage.input_tokens == 5
    assert out.context_wrapper.usage.output_tokens == 10


@pytest.mark.asyncio
async def test_lightweight_reparse_recovers_fenced_json():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_chat_response('```json\n{"vulnerabilities": []}\n```'))
    p = _provider_with_client(client)
    out = await p._lightweight_reparse("text", {"type": "object"}, "m")
    assert out.final_output == {"vulnerabilities": []}


@pytest.mark.asyncio
async def test_lightweight_reparse_returns_none_on_garbage():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_chat_response("仍然不是 JSON"))
    p = _provider_with_client(client)
    assert await p._lightweight_reparse("text", {"type": "object"}, "m") is None


@pytest.mark.asyncio
async def test_lightweight_reparse_returns_none_on_api_error():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("boom"))
    p = _provider_with_client(client)
    assert await p._lightweight_reparse("text", {"type": "object"}, "m") is None


@pytest.mark.asyncio
async def test_lightweight_reparse_skips_when_no_schema_or_text():
    client = MagicMock()
    p = _provider_with_client(client)
    assert await p._lightweight_reparse("text", None, "m") is None
    assert await p._lightweight_reparse("", {"type": "object"}, "m") is None
    client.chat.completions.create.assert_not_called()


@pytest.mark.asyncio
async def test_lightweight_reparse_stall_times_out_not_hang(monkeypatch):
    """reparse 请求永久 stall -> 必须在 reparse 超时内返回 None（不得被 SDK 内部
    重试拖到分钟级）。回归 2026-08-18 xss-vuln 同类：reparse 在 call() 的
    wait_for(call_timeout) 之外执行，非流式 create + client 级 max_retries 放大
    （4 次×300s）时不受任何 wall-clock 约束。修：包 wait_for 独立短超时
    （SUPERNOVA_OPENAI_REPARSE_TIMEOUT，默认 120s）。"""
    import asyncio
    import time as _time

    monkeypatch.setenv("SUPERNOVA_OPENAI_REPARSE_TIMEOUT", "0.5")

    async def _stall(**kwargs):
        await asyncio.Event().wait()  # 永不返回 → 模拟后端 stall

    client = MagicMock()
    client.chat.completions.create = _stall
    p = _provider_with_client(client)

    t0 = _time.monotonic()
    # 外层 10s 保底：实现缺 wait_for 时这里 TimeoutError → 测试红（而非挂死 suite）
    out = await asyncio.wait_for(
        p._lightweight_reparse("analysis text", {"type": "object"}, "m"),
        timeout=10)
    elapsed = _time.monotonic() - t0

    assert out is None              # 优雅降级（进 L2），不抛
    assert elapsed < 10             # stall 被独立超时掐断（默认 120s，测试 0.5s）


def test_reparse_timeout_default_and_env(monkeypatch):
    """_reparse_timeout：env 未设默认 120s；SUPERNOVA_OPENAI_REPARSE_TIMEOUT 可覆盖。"""
    import os
    monkeypatch.delenv("SUPERNOVA_OPENAI_REPARSE_TIMEOUT", raising=False)
    p = _provider_with_client(MagicMock())
    assert p._reparse_timeout() == 120.0
    monkeypatch.setenv("SUPERNOVA_OPENAI_REPARSE_TIMEOUT", "45")
    assert p._reparse_timeout() == 45.0
