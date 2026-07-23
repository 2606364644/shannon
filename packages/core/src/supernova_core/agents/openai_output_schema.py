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


# 实现已下沉到无 SDK 依赖的 llm_json.py（code_index 等核心层可安全复用，不必拖
# openai-agents SDK 进 import 链）。此处 re-export 保持 providers_anthropic /
# providers_openai 现有 `from .openai_output_schema import _extract_json_payload` 不破。
from .llm_json import _extract_json_payload


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
        # L0 容错解析：剥 fence + 子串提取（模拟 Claude SDK 的 LLM→JSON 接管契约；
        # TS 侧 SDK 免费，openai-agents 无此层，Python 自己补）。
        candidate = _extract_json_payload(json_str)
        if candidate is None:
            raise StructuredOutputParseError(json_str)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            # 含 {} 但 JSON 语法坏（非法反斜杠转义，如正则 \d、Windows 路径 \U）：
            # _extract_json_payload 已提取到 candidate，失败发生在 json.loads 这步。
            # 转成 StructuredOutputParseError 走 L1 轻量重输兜底（providers_openai
            # except StructuredOutputParseError → _lightweight_reparse），而非裸
            # JSONDecodeError 冒泡成 AgentExecutionError retryable（2026-07-22
            # auth-vuln ``Invalid \escape`` 失败根因——L0 此前未捕获该分支）。
            raise StructuredOutputParseError(json_str) from e
