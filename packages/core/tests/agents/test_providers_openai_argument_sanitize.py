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


# ---- 「合法 JSON 非 object」根类型兜底（回归 __legacy__ NodeGoat-20260820-162849）----


@pytest.mark.parametrize("bad_args", ["[]", "[null]", '"text"', "123", "true", ""])
def test_sanitize_fallbacks_when_valid_json_root_is_not_object(bad_args):
    """合法 JSON 但根非 object（或空串）→ 兜 "{}"。

    回归真机 NodeGoat-20260820-162849 pre-recon：deepseek-v4-flash 一批 7 个
    set_* 并行调用中 set_xss_sinks arguments 退化为 "[]"；防线1 bridge 正确
    拒收让模型重发，但防线2 只验「合法 JSON 串」原样放行 → 网关 parse 后
    .items() 于 list → 400 'list' object has no attribute 'items' → 整跑阵亡。
    """
    msgs = [_assistant_with_tool_call(bad_args)]
    _sanitize_messages(msgs)
    assert msgs[0]["tool_calls"][0]["function"]["arguments"] == "{}"


def test_sanitize_valid_object_with_nested_array_still_unchanged():
    """收紧契约不误伤：object 根（值里含数组/null）原样保留。"""
    good = '{"sinks": [{"a": 1}], "n": null}'
    msgs = [_assistant_with_tool_call(good)]
    _sanitize_messages(msgs)
    assert msgs[0]["tool_calls"][0]["function"]["arguments"] == good


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


# ---- thinking 禁用注入（2026-09-01 NodeGoat-20260901-015018 事故驱动）----
# 背景：deepseek-v4-flash-0731 等推理快照默认开 thinking 且 reasoning token 计入
# completion_tokens——chain verdict 单链 mean 133s（每轮 output 745 tok，对比 coder
# 版 154 tok），72 链判定量撑爆 15min×3 窗口。实测（2026-09-01 llm-proxy A/B）：
# 请求体加 ``thinking: {"type": "disabled"}`` 是该网关唯一有效开关（deepseek 官方
# chat_template_kwargs 风格被网关忽略），单轮 19s→7s、completion -73%、reasoning 归零。
# 注入做在 client 包装层（所有 chat.completions.create 必经）= 整个扫描全部 LLM
# 请求统一生效（多轮 agent / 单次调用 / subagent 共用同一 client）。


@pytest.mark.asyncio
async def test_wrap_client_injects_thinking_disabled_when_flag_set():
    raw = MagicMock()
    raw.chat.completions.create = AsyncMock(return_value="resp")
    wrapped = _wrap_client_for_argument_sanitize(raw, disable_thinking=True)
    await wrapped.chat.completions.create(model="m", messages=[])
    assert raw.chat.completions.create.call_args.kwargs["thinking"] == {
        "type": "disabled"}


@pytest.mark.asyncio
async def test_wrap_client_no_thinking_injection_by_default():
    """默认（disable_thinking=False）不注入——保持模型默认行为，零行为变化。"""
    raw = MagicMock()
    raw.chat.completions.create = AsyncMock(return_value="resp")
    wrapped = _wrap_client_for_argument_sanitize(raw)
    await wrapped.chat.completions.create(model="m", messages=[])
    assert "thinking" not in raw.chat.completions.create.call_args.kwargs


@pytest.mark.asyncio
async def test_wrap_client_thinking_not_overridden_when_explicit():
    """调用方显式传 thinking 时不覆盖（setdefault 语义，防御未来细粒度控制）。"""
    raw = MagicMock()
    raw.chat.completions.create = AsyncMock(return_value="resp")
    wrapped = _wrap_client_for_argument_sanitize(raw, disable_thinking=True)
    await wrapped.chat.completions.create(
        model="m", messages=[], thinking={"type": "adaptive"})
    assert raw.chat.completions.create.call_args.kwargs["thinking"] == {
        "type": "adaptive"}


@pytest.mark.asyncio
async def test_provider_wires_disable_thinking_from_config(monkeypatch):
    """ProviderConfig.adaptive_thinking=False → client 包装层带 thinking 禁用。

    对齐 anthropic 引擎同字段语义（providers_anthropic._is_adaptive_thinking_enabled）：
    False=显式禁用；True/None=不动（模型默认）。工作区 config.yaml 的
    adaptive_thinking 字段（ws_env_codec CONFIG_FIELDS 的 SUPERNOVA_ADAPTIVE_THINKING）
    经 build_provider_config 填充至此——工作区一键关整个扫描的 thinking。
    """
    from supernova_core.agents import providers_openai as mod
    from supernova_core.agents.runner import ProviderConfig

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value="resp")

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            pass

        def __getattr__(self, name):
            return getattr(fake_client, name)

    monkeypatch.setattr(mod, "AsyncOpenAI", _FakeAsyncOpenAI)

    provider = mod.OpenAIProvider(
        ProviderConfig(type="openai_compatible", api_key="k",
                       base_url="https://x.example", adaptive_thinking=False))
    client = provider._get_client()
    # 经包装代理发一次请求，断言 thinking 注入
    await client.chat.completions.create(model="m", messages=[])
    assert fake_client.chat.completions.create.call_args.kwargs["thinking"] == {
        "type": "disabled"}


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [True, None])
async def test_provider_no_thinking_wiring_when_config_true_or_none(monkeypatch, flag):
    """adaptive_thinking=True/None → 不注入（默认行为，两引擎语义对齐）。"""
    from supernova_core.agents import providers_openai as mod
    from supernova_core.agents.runner import ProviderConfig

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value="resp")

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            pass

        def __getattr__(self, name):
            return getattr(fake_client, name)

    monkeypatch.setattr(mod, "AsyncOpenAI", _FakeAsyncOpenAI)
    provider = mod.OpenAIProvider(
        ProviderConfig(type="openai_compatible", api_key="k",
                       base_url="https://x.example", adaptive_thinking=flag))
    client = provider._get_client()
    await client.chat.completions.create(model="m", messages=[])
    assert "thinking" not in fake_client.chat.completions.create.call_args.kwargs

