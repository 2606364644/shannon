"""防线2：发包前归一化 tool_call 非法 arguments + 剥离 tools.strict，止血第三方端点。

回归真机 hr-20260730-014845：GLM 吐残缺 arguments → openai-agents 无状态全量重发
→ ARK 消费侧校验「arguments 必须合法 JSON」失败 → 400 Invalid request body。

回归真机 __legacy__ probe-d6168171（2026-08-18）：openai-agents function_tool 默认
strict_mode=True，序列化后每个工具 function 带 "strict": true → litellm 网关
(llm-proxy.futuoa.com) 视作启用 grammar-constrained decoding → 路由到的 DFLASH
投机推理部署不支持 grammar 约束 → 流中途 MidStreamFallbackError，被 openai SDK
吞成泛化 "An error occurred during streaming"。strict 是 OpenAI 专有参数，第三方
兼容端点普遍不认；发包前剥掉，与 arguments 清洗同一防线。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from supernova_core.agents.providers_openai import (
    _sanitize_messages,
    _strip_tools_strict,
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


# ---- tools.strict 剥离（回归 __legacy__ probe-d6168171 litellm/DFLASH）----


def _tools_with_strict():
    """openai-agents function_tool(strict_mode=True) 序列化后的典型 tools 结构。"""
    return [
        {"type": "function", "function": {
            "name": "bash", "description": "run shell",
            "parameters": {"type": "object", "properties": {}},
            "strict": True,
        }},
        {"type": "function", "function": {
            "name": "read_file", "description": "read",
            "parameters": {"type": "object", "properties": {}},
            "strict": True,
        }},
    ]


def test_strip_tools_strict_removes_strict_key():
    tools = _tools_with_strict()
    _strip_tools_strict(tools)
    for t in tools:
        assert "strict" not in t["function"]


def test_strip_tools_strict_preserves_other_function_fields():
    tools = _tools_with_strict()
    _strip_tools_strict(tools)
    assert tools[0]["function"]["name"] == "bash"
    assert tools[0]["function"]["parameters"] == {"type": "object", "properties": {}}
    assert tools[0]["type"] == "function"


def test_strip_tools_strict_noop_when_absent():
    """无 strict 字段的 tools 不受影响（含完全无 function key 的畸形项）。"""
    tools = [{"type": "function", "function": {
        "name": "x", "parameters": {}, "description": "d"}}]
    before = [dict(t) for t in tools]
    _strip_tools_strict(tools)
    assert tools == before


def test_strip_tools_strict_handles_missing_tools_kwarg():
    """tools 为 None / 空时不崩。"""
    _strip_tools_strict(None)
    _strip_tools_strict([])
    _strip_tools_strict("not a list")  # 非列表静默跳过


@pytest.mark.asyncio
async def test_wrap_client_strips_strict_before_create():
    raw = MagicMock()
    raw.chat.completions.create = AsyncMock(return_value="resp")
    wrapped = _wrap_client_for_argument_sanitize(raw)
    await wrapped.chat.completions.create(
        model="m", messages=[], tools=_tools_with_strict())
    sent_tools = raw.chat.completions.create.call_args.kwargs["tools"]
    for t in sent_tools:
        assert "strict" not in t["function"]


@pytest.mark.asyncio
async def test_wrap_client_strips_strict_preserves_messages_sanitization():
    """剥 strict 不破坏原有 arguments 清洗（两道防线共存于同一发包拦截）。"""
    raw = MagicMock()
    raw.chat.completions.create = AsyncMock(return_value="resp")
    wrapped = _wrap_client_for_argument_sanitize(raw)
    await wrapped.chat.completions.create(
        model="m",
        messages=[_assistant_with_tool_call('{"a":')],
        tools=_tools_with_strict())
    kw = raw.chat.completions.create.call_args.kwargs
    assert kw["messages"][0]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert "strict" not in kw["tools"][0]["function"]
