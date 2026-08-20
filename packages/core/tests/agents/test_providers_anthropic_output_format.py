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
