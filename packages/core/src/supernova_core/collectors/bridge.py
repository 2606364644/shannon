"""双引擎工具桥：一份 SectionSchema 在 openai / claude 各生成一套 set_* / append 工具。

消除「13 agent × 2 引擎 = 26 套手写工具」——两引擎共享输入都是 JSON Schema dict:
- openai: 直接构造 FunctionTool(params_json_schema=<dict>, strict_json_schema=False,
          on_invoke_tool=(ctx, json_str))。不走 @function_tool 签名推断。
- claude: SdkMcpTool(input_schema=<完整 JSON Schema dict>)，create_sdk_mcp_server 的
          _build_schema 检测到 type+properties 原样透传（claude_agent_sdk/__init__.py:403-421）。

按 SectionSchema.mode 分支：
- mode="set"（默认）：闭包调 collector.set_section，DuplicateCallError 时：
    - openai 返错误串（不 raise，不 fail run，对齐 TS retryable=false 结构化结果）
    - claude 返 {"is_error": True} 信封
  首次调用生效。
- mode="append"：闭包调 collector.append_section（不抛 DuplicateError），返
    ``f"{tool_name}: recorded (N total)"``（N=当前累积数）。append 可多次调。
"""
from __future__ import annotations

import json

from supernova_core.collectors.base import CollectorBase, DuplicateCallError, SectionSchema


def build_openai_tools(collector: CollectorBase):
    """每个 SectionSchema -> 一个 openai-agents FunctionTool（闭包捕获 collector）。"""
    return [_make_openai_function_tool(collector, s) for s in collector.section_schemas]


def _make_openai_function_tool(collector: CollectorBase, schema: SectionSchema):
    from agents import FunctionTool

    tool_name = schema.tool_name

    if schema.mode == "append":
        async def _on_invoke_append(ctx, input_json: str) -> str:
            try:
                payload = json.loads(input_json) if input_json else {}
            except json.JSONDecodeError:
                payload = {}
            collector.append_section(tool_name, payload)
            items = collector.get_all().get(schema.section_key, [])
            return f"{tool_name}: recorded ({len(items)} total)"

        on_invoke = _on_invoke_append
    else:
        async def _on_invoke_set(ctx, input_json: str) -> str:
            # ctx 不用（collector 经闭包）；input_json 是模型输出的 JSON 串
            try:
                payload = json.loads(input_json) if input_json else {}
            except json.JSONDecodeError:
                payload = {}
            try:
                collector.set_section(tool_name, payload)
            except DuplicateCallError:
                return f"{tool_name}: DuplicateError — already called; first call wins"
            return f"{tool_name}: recorded"

        on_invoke = _on_invoke_set

    return FunctionTool(
        name=tool_name,
        description=schema.description,
        params_json_schema=schema.json_schema,
        on_invoke_tool=on_invoke,
        strict_json_schema=False,
    )


def build_claude_mcp_server(
    collector: CollectorBase, server_name: str = "shannon-collector"
):
    """每个 SectionSchema -> 一个 SdkMcpTool，打包成 in-process MCP server（无子进程/IPC）。"""
    from claude_agent_sdk import create_sdk_mcp_server

    tools = [_make_claude_sdk_tool(collector, s) for s in collector.section_schemas]
    return create_sdk_mcp_server(name=server_name, tools=tools)


def _make_claude_sdk_tool(collector: CollectorBase, schema: SectionSchema):
    from claude_agent_sdk import SdkMcpTool

    tool_name = schema.tool_name

    if schema.mode == "append":
        async def _handler_append(args: dict) -> dict:
            collector.append_section(tool_name, args or {})
            items = collector.get_all().get(schema.section_key, [])
            return {
                "content": [
                    {"type": "text", "text": f"{tool_name}: recorded ({len(items)} total)"}
                ]
            }

        handler = _handler_append
    else:
        async def _handler_set(args: dict) -> dict:
            try:
                collector.set_section(tool_name, args or {})
            except DuplicateCallError:
                return {
                    "content": [
                        {"type": "text", "text": f"{tool_name}: DuplicateError — already called; first call wins"}
                    ],
                    "is_error": True,
                }
            return {"content": [{"type": "text", "text": f"{tool_name}: recorded"}]}

        handler = _handler_set

    return SdkMcpTool(
        name=tool_name,
        description=schema.description,
        input_schema=schema.json_schema,
        handler=handler,
    )
