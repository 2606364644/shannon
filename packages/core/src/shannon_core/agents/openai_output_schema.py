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


class StructuredOutputParseError(Exception):
    """openai 引擎 structured output 解析失败（L0 容错后仍无法提取合法 JSON）。

    不继承 ModelBehaviorError：避免被 openai-agents SDK 的 error handler 路径
    误吞，确保由 providers_openai 的 L1/L2 显式处理。承载 OUTPUT_VALIDATION_FAILED
    语义（对齐 TS message-handlers.ts:355）。
    """


def _extract_json_payload(text: str) -> str | None:
    """从 LLM 输出文本提取 JSON 字符串（L0/L1 复用）。

    模拟 Claude SDK「把 LLM 文本变成合法 JSON」的契约（TS 侧 SDK 免费；
    openai-agents 无此层，Python 自己补）。处理 GLM 常见收尾形态：
      1. markdown fence 包裹（```json ... ``` / ``` ... ```）；
      2. 前导叙述 + JSON（取首个 { 到末个 } 的子串）。
    全无 { / } → 返回 None（调用方据此抛 StructuredOutputParseError）。
    """
    if not text:
        return None
    s = text.strip()
    if not s:
        return None
    if s.startswith("```"):
        lines = s.splitlines()
        if lines:
            lines = lines[1:]            # 去首行 ```（含可能的语言标签）
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]        # 去末行 ```
            s = "\n".join(lines).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return s[start : end + 1]


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
