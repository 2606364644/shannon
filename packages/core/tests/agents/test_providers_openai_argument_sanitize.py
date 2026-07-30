"""防线2：发包前归一化 tool_call 非法 arguments，止血第三方端点 400。

回归真机 hr-20260730-014845：GLM 吐残缺 arguments → openai-agents 无状态全量重发
→ ARK 消费侧校验「arguments 必须合法 JSON」失败 → 400 Invalid request body。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from supernova_core.agents.providers_openai import (
    _sanitize_messages,
    _wrap_client_for_argument_sanitize,
)


def _assistant_with_tool_call(args: str) -> dict:
    return {"role": "assistant", "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": "set_application_intelligence", "arguments": args}}]}


def test_sanitize_repairs_markdown_fenced_arguments():
    msgs = [_assistant_with_tool_call("```json\n{\"a\": 1}\n```")]
    _sanitize_messages(msgs)
    assert msgs[0]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'


def test_sanitize_fallbacks_to_empty_object_when_unrepairable():
    msgs = [_assistant_with_tool_call('{"architecture":')]  # 截断，修不好
    _sanitize_messages(msgs)
    assert msgs[0]["tool_calls"][0]["function"]["arguments"] == "{}"


def test_sanitize_leaves_valid_arguments_unchanged():
    msgs = [_assistant_with_tool_call('{"a": 1}')]
    _sanitize_messages(msgs)
    assert msgs[0]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'


def test_sanitize_leaves_non_tool_messages_untouched():
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok"},
            {"role": "tool", "tool_call_id": "c1", "content": "recorded"}]
    before = [dict(m) for m in msgs]
    _sanitize_messages(msgs)
    assert msgs == before


@pytest.mark.asyncio
async def test_wrap_client_sanitizes_before_create():
    raw = MagicMock()
    raw.chat.completions.create = AsyncMock(return_value="resp")
    wrapped = _wrap_client_for_argument_sanitize(raw)
    await wrapped.chat.completions.create(
        model="m", messages=[_assistant_with_tool_call('{"a":')])
    sent = raw.chat.completions.create.call_args.kwargs["messages"]
    assert sent[0]["tool_calls"][0]["function"]["arguments"] == "{}"


@pytest.mark.asyncio
async def test_wrap_client_passes_through_other_kwargs():
    raw = MagicMock()
    raw.chat.completions.create = AsyncMock(return_value="resp")
    wrapped = _wrap_client_for_argument_sanitize(raw)
    await wrapped.chat.completions.create(model="m", messages=[], temperature=0.5, stream=True)
    kw = raw.chat.completions.create.call_args.kwargs
    assert kw["temperature"] == 0.5 and kw["stream"] is True


def test_wrap_client_proxies_other_attributes():
    """base_url 等非 chat 属性原样透传，避免漏转发破坏 SDK（如 trace 取 base_url）。"""
    raw = MagicMock()
    raw.base_url = "https://ark.example.com"
    _ = raw.chat.completions  # 触发 MagicMock 链构造
    wrapped = _wrap_client_for_argument_sanitize(raw)
    assert wrapped.base_url == "https://ark.example.com"
