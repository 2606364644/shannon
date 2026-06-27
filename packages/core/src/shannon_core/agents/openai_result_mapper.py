"""openai-agents RunResult → shannon ClaudeRunResult 映射（纯函数，无副作用）。"""
from __future__ import annotations

import json
from typing import Any

from agents import RunResult

from .runner import ClaudeRunResult, TokenUsage

# GLM/openai endpoint 不支持计费归集，cost 留 0.0（不假估算），以 provider 账单为准。
# 此 0 值对 spending-cap 兜底无害：utils/billing.is_spending_cap_behavior 的
# cost>0→False 早退逻辑意味着 cost=0 时继续走 text 关键词匹配（C1，已核验）。


def _usage_from(run_result: RunResult) -> TokenUsage:
    usage = getattr(getattr(run_result, "context_wrapper", None), "usage", None)
    if usage is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
    )


def map_run_result(
    run_result: RunResult,
    *,
    duration_ms: int,
    model: str,
    turns: int,
    stop_reason: str | None = None,
    output_format: dict | None = None,
) -> ClaudeRunResult:
    final = getattr(run_result, "final_output", "")
    # B2: 结构化输出路径下 final_output 可能是 dict（RawJsonSchemaOutputSchema.validate_json 返回）
    if isinstance(final, str):
        text = final
    else:
        text = json.dumps(final, ensure_ascii=False) if not isinstance(final, (int, float, bool)) else str(final)
    tokens = _usage_from(run_result)

    structured_output: Any | None = None
    if output_format:
        # 结构化输出：优先用已解析的 dict；退化到 json.loads 文本
        if isinstance(final, (dict, list)):
            structured_output = final
        elif isinstance(final, str) and text:
            try:
                structured_output = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                structured_output = None

    # B1: max_turns 对齐 Claude subtype=error_max_turns → 失败 + 不可重试（spec §1.2）
    is_max_turns = stop_reason == "max_turns"

    return ClaudeRunResult(
        text=text,
        success=not is_max_turns,
        duration=duration_ms,
        turns=turns,
        cost=0.0,  # 见文件头注释（C1）
        model=model,
        structured_output=structured_output,
        tokens=tokens,
        stop_reason=stop_reason,
        error_code="ExecutionLimitError" if is_max_turns else None,
        retryable=False if is_max_turns else True,
    )
