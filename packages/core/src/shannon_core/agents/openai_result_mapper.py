"""openai-agents RunResult → shannon ClaudeRunResult 映射（纯函数，无副作用）。"""
from __future__ import annotations

import json
import logging
from typing import Any

from agents import RunResult

from .pricing import compute_cost, is_model_priced, normalize_model
from .runner import ClaudeRunResult, TokenUsage

_log = logging.getLogger(__name__)
_WARNED_UNKNOWN_MODELS: set[str] = set()

# cost 由 pricing.compute_cost 按 token 用量 × 价目表换算（本币直达，per-profile 可经
# SHANNON_PRICING_OVERRIDE 覆盖价表/币种）；未知模型回落 0.0 + warning（守「不假估算」）。
# spending-cap 文本检测（utils/billing.is_spending_cap_behavior）的 cost>0→False 早退
# 使该检测对 cost>0 引擎失效——这是已接受的不变量（与 claude 引擎一致），
# 真正限额检测靠结构化错误码（executor.api_error_status）。详见
# docs/superpowers/specs/2026-06-29-openai-engine-cost-accounting-design.md §4.6。


def _usage_from(run_result: RunResult) -> TokenUsage:
    usage = getattr(getattr(run_result, "context_wrapper", None), "usage", None)
    if usage is None:
        return TokenUsage()
    details = getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details is not None else 0
    cached = cached or 0
    raw_input = getattr(usage, "input_tokens", 0) or 0
    billable_input = max(raw_input - cached, 0)  # 归一为「不含 cache 命中」（spec §4.3）
    return TokenUsage(
        input_tokens=billable_input,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_input_tokens=cached,
        cache_creation_input_tokens=0,  # openai 协议无此概念（自动缓存、无创建费）
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
    cost_amount = compute_cost(model, tokens)
    cost = cost_amount.cost
    if model and cost == 0.0 and not is_model_priced(model):
        norm = normalize_model(model)
        if norm not in _WARNED_UNKNOWN_MODELS:
            _WARNED_UNKNOWN_MODELS.add(norm)
            _log.warning(
                "openai 引擎成本核算：模型 %r 未在价目表中，cost 回落 0.0（不假估算）。"
                " 可经 SHANNON_PRICING_OVERRIDE 补充。",
                model,
            )

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
        cost=cost,
        cost_currency=cost_amount.currency,
        model=model,
        structured_output=structured_output,
        tokens=tokens,
        stop_reason=stop_reason,
        error_code="ExecutionLimitError" if is_max_turns else None,
        retryable=False if is_max_turns else True,
    )
