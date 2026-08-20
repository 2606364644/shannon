"""锚点测试:AnthropicProvider._build_options 把裸 JSON Schema 包装成
claude_agent_sdk 的信封契约 ``{type:'json_schema', schema:{...}}``。

背景(claude_agent_sdk subprocess_cli.py:395-404):SDK 仅当
``output_format['type'] == 'json_schema'`` 时才提取 ``schema`` 并加 ``--json-schema``
CLI 参数(启用协议级结构化输出 + AJV 校验 + SDK error_max_structured_output_retries
重试)。传裸 schema(type='object')会被忽略 → CLI 退化为 best-effort 文本提取 →
vuln agent exploitation queue 概率性漏盘(NodeGoat injection 3 连跪、auth attempt1 漏)。

业务层返回裸 schema(不感知引擎,CLAUDE.md §2)——现行活跃用户如 executor 定向重查的
``_RECHECK_OUTPUT_SCHEMA``、authz gitnexus judge 的 inline schema(vuln agent Phase 2 起
走 collector 主通道,不再传 schema);此信封包装是 AnthropicProvider 的职责 ——
openai 引擎直接用裸 schema(``RawJsonSchemaOutputSchema``)。
对齐 TS queue-schemas.ts:106 toOutputFormat + SDK types.py:1894 docstring。
"""
from supernova_core.agents.providers_anthropic import AnthropicProvider
from supernova_core.agents.runner import ProviderConfig


class TestBuildOptionsOutputFormatEnvelope:
    """_build_options 的 output_format 信封包装(对齐 claude_agent_sdk 契约)。"""

    def test_wraps_bare_schema_into_json_schema_envelope(self, tmp_path):
        """裸 schema 必须被包装成 {type:'json_schema', schema:{...}} 信封,
        否则 subprocess_cli.py:400 不认 → --json-schema 不加 → 概率性漏盘。"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        bare = {
            "type": "object",
            "properties": {"vulnerabilities": {"type": "array"}},
            "required": ["vulnerabilities"],
        }
        options = provider._build_options(
            cwd=str(tmp_path), model="claude-sonnet-4-6", output_format=bare
        )
        assert options.output_format == {"type": "json_schema", "schema": bare}

    def test_no_envelope_when_output_format_none(self, tmp_path):
        """output_format=None(非结构化 agent,如 recon/report)不包装,保持 None。"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        options = provider._build_options(
            cwd=str(tmp_path), model="claude-sonnet-4-6", output_format=None
        )
        assert options.output_format is None

    def test_inner_schema_preserved_verbatim(self, tmp_path):
        """包装后内层 schema 必须原样保留(不丢 required/properties/items 等字段),
        确保 AJV 校验拿到完整 schema。"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        bare = {
            "type": "object",
            "properties": {
                "vulnerabilities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["ID", "vulnerability_type"],
                        "additionalProperties": True,
                    },
                },
            },
            "required": ["vulnerabilities"],
        }
        options = provider._build_options(
            cwd=str(tmp_path), model="claude-sonnet-4-6", output_format=bare
        )
        assert options.output_format["type"] == "json_schema"
        # 内层 schema 原样保留(同一对象,无字段丢失/改写)
        assert options.output_format["schema"] == bare


# ── P2（dataflow-view 2026-08-20 Task 3）：bridge 双引擎 schema 一致性 ──

def test_bridge_dataflow_steps_in_both_engines():
    """P2: dataflow_steps 一份 schema 出两套工具（bridge 单点定义不变量）。

    bridge 真实 API 吃 CollectorBase（非 sections 列表）：
    - openai: ``build_openai_tools(collector)`` → FunctionTool.params_json_schema。
    - claude: ``build_claude_mcp_server(collector)`` 返回 McpSdkServerConfig
      （``{"type":"sdk","name",...,"instance": mcp.server.Server}`），工具 schema 经
      SDK ``_build_schema``（type+properties → 原样透传）进入 server 注册的
      list_tools handler——驱动该 handler 拿 Tool.inputSchema，即 CLI 实际发给
      模型的 schema。断言两引擎同含 dataflow_steps 且内容一致。
    """
    import asyncio

    from mcp.types import ListToolsRequest

    from supernova_core.collectors.bridge import build_claude_mcp_server, build_openai_tools
    from supernova_core.collectors.vuln import make_vuln_collector

    collector = make_vuln_collector("injection")

    # openai 引擎：FunctionTool（strict_json_schema=False，宽容解析）
    oai_tools = build_openai_tools(collector)
    oai_tool = next(t for t in oai_tools if t.name == "submit_finding")
    oai_props = oai_tool.params_json_schema["properties"]
    assert oai_tool.strict_json_schema is False

    # claude 引擎：in-process MCP server 的 list_tools → Tool.inputSchema
    server = build_claude_mcp_server(collector)["instance"]
    handler = server.request_handlers[ListToolsRequest]
    result = asyncio.run(handler(ListToolsRequest(method="tools/list")))
    claude_tool = next(t for t in result.root.tools if t.name == "submit_finding")
    claude_props = claude_tool.inputSchema["properties"]

    assert "dataflow_steps" in oai_props
    assert "dataflow_steps" in claude_props
    # 同一份 dict：openai 侧 bridge 直接引用 SectionSchema.json_schema（同一对象）；
    # claude 侧经 pydantic Tool 校验是等值拷贝，故用 == 断言内容一致。
    section = collector.section_schemas[0]
    assert section.tool_name == "submit_finding"
    assert oai_tool.params_json_schema is section.json_schema
    assert oai_props["dataflow_steps"] == claude_props["dataflow_steps"]
