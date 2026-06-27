"""B2（2026-06-27 双引擎解耦修复）：openai-agents 结构化输出适配器。

问题：openai-agents 的 Agent(output_type=...) 只接受 Python type 或
AgentOutputSchemaBase，不吃 Claude 风格的 JSON Schema dict。providers_openai
此前的 build_agent 丢弃 output_format，structured_output 纯靠 json.loads 兜底，
模型输出非纯 JSON 时 auth verdict 静默失败。

解法：RawJsonSchemaOutputSchema 直接持有原始 JSON Schema dict，实现
AgentOutputSchemaBase 的 5 个抽象方法，绕过 Pydantic 建模（无 round-trip 损耗）。
openai-agents 会把 json_schema() 透传给 OpenAI response_format，约束模型输出。

降级：GLM 若不支持 response_format json_schema，validate_json 仍用 json.loads，
final_output 走 map_run_result 兜底（best-effort，见 spec §5）。
"""
from __future__ import annotations

import json
from typing import Any

from agents import AgentOutputSchemaBase


class RawJsonSchemaOutputSchema(AgentOutputSchemaBase):
    """持有原始 JSON Schema 的 AgentOutputSchemaBase 实现（non-strict）。"""

    def __init__(self, schema: dict[str, Any]):
        self._schema = schema

    def is_plain_text(self) -> bool:
        return False

    def is_strict_json_schema(self) -> bool:
        # GLM 第三方 endpoint 用 non-strict，避免 strict 模式对额外字段拒收
        return False

    def json_schema(self) -> dict[str, Any]:
        return self._schema

    def name(self) -> str:
        return "shannon_raw_json_schema"

    def validate_json(self, json_str: str) -> Any:
        # best-effort：仅解析 JSON，不做 schema 完整校验（GLM 已被 response_format 约束）
        return json.loads(json_str)
