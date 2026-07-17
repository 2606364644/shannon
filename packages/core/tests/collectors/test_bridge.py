import json

import pytest

from shannon_core.collectors.base import CollectorBase, SectionSchema
from shannon_core.collectors.bridge import build_claude_mcp_server, build_openai_tools

SCHEMA = SectionSchema(
    tool_name="set_alpha",
    section_key="alpha",
    description="alpha tool",
    json_schema={
        "type": "object",
        "properties": {"x": {"type": "string", "minLength": 1}},
        "required": ["x"],
    },
)

APPEND_SCHEMA = SectionSchema(
    tool_name="set_eps",
    section_key="eps",
    description="append tool",
    json_schema={
        "type": "object",
        "properties": {"x": {"type": "string", "minLength": 1}},
        "required": ["x"],
    },
    mode="append",
)


def _collector():
    return CollectorBase([SCHEMA])


def _append_collector():
    return CollectorBase([APPEND_SCHEMA])


# ---------- openai ----------

@pytest.mark.asyncio
async def test_openai_tool_invocation_writes_collector():
    from agents import RunContextWrapper

    collector = _collector()
    (tool,) = build_openai_tools(collector)
    assert tool.name == "set_alpha"
    assert tool.params_json_schema == SCHEMA.json_schema
    assert tool.strict_json_schema is False

    result = await tool.on_invoke_tool(RunContextWrapper(context=None), json.dumps({"x": "v"}))
    assert "recorded" in str(result)
    assert collector.get_all() == {"alpha": {"x": "v"}}


@pytest.mark.asyncio
async def test_openai_tool_duplicate_returns_error_string_not_raise():
    from agents import RunContextWrapper

    collector = _collector()
    (tool,) = build_openai_tools(collector)
    await tool.on_invoke_tool(RunContextWrapper(context=None), json.dumps({"x": "first"}))
    result = await tool.on_invoke_tool(RunContextWrapper(context=None), json.dumps({"x": "second"}))
    assert "DuplicateError" in str(result)          # 返错误串，不 raise、不 fail run
    assert collector.get_all() == {"alpha": {"x": "first"}}


# ---------- claude ----------

@pytest.mark.asyncio
async def test_claude_mcp_server_is_in_process_sdk_config():
    collector = _collector()
    server = build_claude_mcp_server(collector)
    assert server["type"] == "sdk"
    assert server["name"] == "shannon-collector"


@pytest.mark.asyncio
async def test_claude_sdk_tool_input_schema_is_full_json_schema():
    from shannon_core.collectors.bridge import _make_claude_sdk_tool

    collector = _collector()
    sdk_tool = _make_claude_sdk_tool(collector, SCHEMA)
    assert sdk_tool.name == "set_alpha"
    assert sdk_tool.input_schema == SCHEMA.json_schema


@pytest.mark.asyncio
async def test_claude_sdk_tool_handler_writes_collector():
    from shannon_core.collectors.bridge import _make_claude_sdk_tool

    collector = _collector()
    sdk_tool = _make_claude_sdk_tool(collector, SCHEMA)
    res = await sdk_tool.handler({"x": "v"})
    assert res["content"][0]["type"] == "text"
    assert "recorded" in res["content"][0]["text"]
    assert res.get("is_error") is not True
    assert collector.get_all() == {"alpha": {"x": "v"}}


@pytest.mark.asyncio
async def test_claude_sdk_tool_handler_duplicate_is_error_envelope():
    from shannon_core.collectors.bridge import _make_claude_sdk_tool

    collector = _collector()
    sdk_tool = _make_claude_sdk_tool(collector, SCHEMA)
    await sdk_tool.handler({"x": "first"})
    res = await sdk_tool.handler({"x": "second"})
    assert res.get("is_error") is True
    assert "DuplicateError" in res["content"][0]["text"]
    assert collector.get_all() == {"alpha": {"x": "first"}}


# ---------- append mode (openai + claude symmetric) ----------

@pytest.mark.asyncio
async def test_openai_append_tool_invocation_accumulates_and_reports_count():
    from agents import RunContextWrapper

    collector = _append_collector()
    (tool,) = build_openai_tools(collector)
    assert tool.name == "set_eps"
    # 第一次 append
    r1 = await tool.on_invoke_tool(RunContextWrapper(context=None), json.dumps({"x": "a"}))
    assert "recorded" in str(r1)
    assert "(1 total)" in str(r1)
    # 第二次 append（不抛 DuplicateError）
    r2 = await tool.on_invoke_tool(RunContextWrapper(context=None), json.dumps({"x": "b"}))
    assert "(2 total)" in str(r2)
    # 累积成 list
    assert collector.get_all() == {"eps": [{"x": "a"}, {"x": "b"}]}
    assert collector.get_call_status()["set_eps"] == "called"


@pytest.mark.asyncio
async def test_claude_append_tool_handler_accumulates_and_reports_count():
    from shannon_core.collectors.bridge import _make_claude_sdk_tool

    collector = _append_collector()
    sdk_tool = _make_claude_sdk_tool(collector, APPEND_SCHEMA)
    res1 = await sdk_tool.handler({"x": "a"})
    assert res1["content"][0]["type"] == "text"
    assert "(1 total)" in res1["content"][0]["text"]
    assert res1.get("is_error") is not True
    res2 = await sdk_tool.handler({"x": "b"})
    assert "(2 total)" in res2["content"][0]["text"]
    assert res2.get("is_error") is not True
    assert collector.get_all() == {"eps": [{"x": "a"}, {"x": "b"}]}
