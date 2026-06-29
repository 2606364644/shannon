"""L1 provider 轻量重输：L0 容错失败后发单个 chat completion 让 GLM 把分析转纯 JSON。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from shannon_core.agents.providers_openai import OpenAIProvider, _ReparsedRunResult
from shannon_core.agents.runner import ProviderConfig


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
