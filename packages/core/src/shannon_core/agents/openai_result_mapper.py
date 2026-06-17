"""openai-agents RunResult → shannon ClaudeRunResult 映射（纯函数，无副作用）。"""
from __future__ import annotations

import json
from typing import Any

from agents import RunResult

from .runner import ClaudeRunResult, TokenUsage

# 与 providers_openai 现有定价表共用；未知模型回退到 gpt-4o 档
_DEFAULT_PRICING = {"input": 0.0025, "output": 0.01}


def _estimate_cost(model: str, tokens: TokenUsage) -> float:
    # GLM 等模型定价未知，这里给 0；真实成本以 provider 账单为准。
    # 保留估算入口，后续可按模型补定价表。
    pricing = _DEFAULT_PRICING
    return (tokens.input_tokens / 1000) * pricing["input"] + (tokens.output_tokens / 1000) * pricing["output"]


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
        cost=_estimate_cost(model, tokens),
        model=model,
        structured_output=structured_output,
        tokens=tokens,
        stop_reason=stop_reason,
    )
