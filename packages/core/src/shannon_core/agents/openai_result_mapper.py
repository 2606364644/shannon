"""openai-agents RunResult → shannon ClaudeRunResult 映射（纯函数，无副作用）。"""
from __future__ import annotations

import json
from typing import Any

from agents import RunResult

from .runner import ClaudeRunResult, TokenUsage

# GLM 等第三方模型定价未知，cost 留 0.0（不假估算），以 provider 账单为准。
# 不复用 providers_openai.OPENAI_PRICING——Task 8 会重写该模块并删除该表。


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
    text = final if isinstance(final, str) else str(final)
    tokens = _usage_from(run_result)

    structured_output: Any | None = None
    if output_format and text:
        try:
            structured_output = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            structured_output = final if not isinstance(final, str) else None

    return ClaudeRunResult(
        text=text,
        success=True,
        duration=duration_ms,
        turns=turns,
        cost=0.0,  # GLM 定价未知，留空
        model=model,
        structured_output=structured_output,
        tokens=tokens,
        stop_reason=stop_reason,
    )
