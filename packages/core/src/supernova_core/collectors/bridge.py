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

required 字段校验（2026-09-03 NodeGoat 首扫回归）：合法 JSON 但缺 schema
required 字段（如 submit_finding 漏 title）曾与非法 JSON 不同罪——被静默
收录返 "recorded"，模型无感知不补交，空 title 一路裸落 report_data.json。
现与「非法 JSON / 非对象」同待遇：返错让模型重发（含缺失字段名，方便补）。
空值判定：None / 空白串算缺失；bool False、数字 0 是合法实质值不误伤
（externally_exploitable=False 必须放行）。
"""
from __future__ import annotations

import json

from supernova_core.agents.llm_json import repair_json_arguments
from supernova_core.collectors.base import CollectorBase, DuplicateCallError, SectionSchema


def _missing_required(json_schema: dict, payload: dict) -> list[str]:
    """schema 顶层 required 字段中，payload 缺键或值为空的字段名列表。

    空 = None 或空白串（required 字段须有实质值）；bool/int 不做空判定，
    False/0 是合法值（externally_exploitable=False 必须放行）。
    """
    missing: list[str] = []
    for key in (json_schema or {}).get("required") or []:
        if key not in payload:
            missing.append(key)
            continue
        v = payload.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            missing.append(key)
    return missing


def _required_error(tool_name: str, missing: list[str]) -> str:
    return (f"{tool_name}: ERROR — missing required field(s): "
            f"{', '.join(missing)}. Resend {tool_name} with every required "
            f"field filled with a meaningful value.")


def build_openai_tools(collector: CollectorBase):
    """每个 SectionSchema -> 一个 openai-agents FunctionTool（闭包捕获 collector）。"""
    return [_make_openai_function_tool(collector, s) for s in collector.section_schemas]


def _make_openai_function_tool(collector: CollectorBase, schema: SectionSchema):
    from agents import FunctionTool

    tool_name = schema.tool_name

    if schema.mode == "append":
        async def _on_invoke_append(ctx, input_json: str) -> str:
            # 非法 JSON（GLM 偶发残缺/markdown 围栏）→ repair 修；修不好返错让模型重发，
            # 不静默兜底 {} 收空数据（旧逻辑既收空、又让非法串毒化 history 致端点 400）。
            repaired = repair_json_arguments(input_json)
            if repaired is None:
                return (f"{tool_name}: ERROR — arguments is not valid JSON. "
                        f"Resend {tool_name} with valid JSON matching the schema.")
            # 合法 JSON 但不是对象（如 "[null]"）也是畸形形态 → 同样返错让模型重发，
            # 不透传给 append_section（dict(list) 抛 TypeError → SDK 包成
            # "Error running tool ..." → activity 失败白烧一轮 LLM 成本）。
            parsed = json.loads(repaired)
            if not isinstance(parsed, dict):
                return (f"{tool_name}: ERROR — arguments must be a JSON object. "
                        f"Resend {tool_name} with a JSON object matching the schema.")
            missing = _missing_required(schema.json_schema, parsed)
            if missing:
                return _required_error(tool_name, missing)
            collector.append_section(tool_name, parsed)
            items = collector.get_all().get(schema.section_key, [])
            return f"{tool_name}: recorded ({len(items)} total)"

        on_invoke = _on_invoke_append
    else:
        async def _on_invoke_set(ctx, input_json: str) -> str:
            # ctx 不用（collector 经闭包）；input_json 是模型输出的 JSON 串。
            # 非法 JSON（GLM 偶发残缺/markdown 围栏）→ repair 修；修不好返错让模型重发，
            # 不静默兜底 {} 收空数据（旧逻辑既收空、又让非法串毒化 history 致端点 400）。
            repaired = repair_json_arguments(input_json)
            if repaired is None:
                return (f"{tool_name}: ERROR — arguments is not valid JSON. "
                        f"Resend {tool_name} with valid JSON matching the schema.")
            # 合法 JSON 但不是对象（如 "[null]"）也是畸形形态 → 同样返错让模型重发，
            # 不透传给 set_section（dict([None]) 抛 TypeError → SDK 包成
            # "Error running tool ..." → activity 失败白烧一轮 LLM 成本）。
            parsed = json.loads(repaired)
            if not isinstance(parsed, dict):
                return (f"{tool_name}: ERROR — arguments must be a JSON object. "
                        f"Resend {tool_name} with a JSON object matching the schema.")
            missing = _missing_required(schema.json_schema, parsed)
            if missing:
                return _required_error(tool_name, missing)
            try:
                collector.set_section(tool_name, parsed)
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
            missing = _missing_required(schema.json_schema, args or {})
            if missing:
                return {
                    "content": [
                        {"type": "text", "text": _required_error(tool_name, missing)}
                    ],
                    "is_error": True,
                }
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
            missing = _missing_required(schema.json_schema, args or {})
            if missing:
                return {
                    "content": [
                        {"type": "text", "text": _required_error(tool_name, missing)}
                    ],
                    "is_error": True,
                }
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
