"""LLM-based per-function taint analysis.

Replaces the old seed_taints() + analyze_intra() regex-based approach from
propagation_builder.py.  Each function with sinks is analyzed by an LLM that
receives the function source code and sink information, returning structured
JSON (TaintAnalysisResult) which is then converted to IntraResult.

On LLM failure, we conservatively mark *all* parameters as tainted
(over-approximation to avoid false negatives).
"""

import json
import logging
from typing import Callable, Awaitable

from supernova_core.code_index.models import FuncBlock, TypedParameter
from supernova_core.code_index.parameter_models import (
    IntraResult,
    PropagationStep,
    SinkCallSite,
    TaintAnalysisResult,
    TaintPath,
)

logger = logging.getLogger(__name__)

# Type alias for the async LLM client callable
LLMClient = Callable[..., Awaitable[str]]


# ---------------------------------------------------------------------------
# 1. Source truncation
# ---------------------------------------------------------------------------

def truncate_source(
    source: str,
    sink_lines: list[int],
    *,
    max_lines: int = 1200,
    prefix_lines: int = 1000,
    context_lines: int = 30,
) -> str:
    """Truncate long source code for LLM context windows.

    Strategy:
      - If total lines <= max_lines, return unchanged.
      - Otherwise keep the first *prefix_lines* lines plus context windows
        around each sink line (±context_lines).
      - Total output is capped at *max_lines*.
    """
    all_lines = source.split("\n")
    total = len(all_lines)

    if total <= max_lines:
        return source

    if not sink_lines:
        # No sinks — just keep the prefix
        return "\n".join(all_lines[:prefix_lines])

    # Build set of line indices to keep (0-based internally; sink_lines are 1-based)
    keep: set[int] = set(range(min(prefix_lines, total)))

    for sl in sink_lines:
        # Convert 1-based sink line to 0-based index
        center = sl - 1
        lo = max(0, center - context_lines)
        hi = min(total, center + context_lines + 1)
        keep.update(range(lo, hi))

    # Sort kept indices and cap at max_lines
    sorted_indices = sorted(keep)[:max_lines]

    return "\n".join(all_lines[i] for i in sorted_indices)


# ---------------------------------------------------------------------------
# 2. Prompt builder
# ---------------------------------------------------------------------------

def build_taint_prompt(
    block: FuncBlock,
    sinks_in_func: list[SinkCallSite],
    typed_params: list[TypedParameter] | None = None,
) -> str:
    """Build a prompt for LLM taint analysis.

    Includes function metadata, typed parameters (with sources), truncated
    source code, sink details, and the expected JSON schema.
    """
    parts: list[str] = []

    # --- Function metadata ---
    parts.append(f"## Function: {block.function_name}")
    parts.append(f"File: {block.file_path}")
    parts.append(f"Lines: {block.start_line}-{block.end_line}")
    parts.append("")

    # --- Parameters ---
    if block.parameters:
        parts.append("### Parameters:")
        if typed_params:
            param_map = {p.name: p for p in typed_params}
            for pname in block.parameters:
                tp = param_map.get(pname)
                if tp:
                    src = tp.source.value if tp.source else "unknown"
                    type_ann = tp.type_annotation or "unknown"
                    parts.append(f"- {pname}: type={type_ann}, source={src}")
                else:
                    parts.append(f"- {pname}")
        else:
            for pname in block.parameters:
                parts.append(f"- {pname}")
        parts.append("")

    # --- Source code ---
    # Compute sink lines relative to function source for truncation
    sink_lines = [s.line - block.start_line + 1 for s in sinks_in_func if s.line]
    truncated = truncate_source(block.source_code, sink_lines)
    parts.append("### Source code:")
    parts.append("```")
    parts.append(truncated)
    parts.append("```")
    parts.append("")

    # --- Sink call sites ---
    if sinks_in_func:
        parts.append("### Detected sinks:")
        for sink in sinks_in_func:
            receiver = f"{sink.callee_receiver}." if sink.callee_receiver else ""
            parts.append(f"- id: {sink.id}")
            parts.append(f"  call: {receiver}{sink.callee_name}")
            parts.append(f"  line: {sink.line}")
            parts.append(f"  category: {sink.category.value}")
            parts.append(f"  dangerous slots:")
            for slot in sink.dangerous_slots:
                parts.append(f"    - arg_index={slot.arg_index}, slot={slot.slot.value}, expression=\"{slot.expression}\"")
        parts.append("")

    # --- Expected JSON schema ---
    parts.append("### Task")
    parts.append(
        "Analyze the function above for taint propagation from its parameters "
        "to the detected sinks. Return a JSON object with this schema:\n"
    )
    parts.append("```json")
    parts.append(json.dumps({
        "tainted_params": ["param_name"],
        "propagation_paths": [
            {
                "source_param": "param_name",
                "sink_id": "sink_id",
                "sink_arg_index": 0,
                "intermediate_vars": ["var1"],
                "sanitized": False,
                "sanitizer_description": None,
                "post_sanitized_concat": False,
                "confidence": 0.9,
            }
        ],
    }, indent=2))
    parts.append("```")
    parts.append("")
    parts.append(
        "Rules:\n"
        "- tainted_params: list all parameters that can reach a sink\n"
        "- propagation_paths: one entry per param->sink path\n"
        "- post_sanitized_concat: true if the path is sanitized but then re-tainted "
        "(e.g. escape() result concatenated with raw input, or merged with another source)\n"
        "- confidence: 0.0-1.0, how certain the taint reaches the sink\n"
        "- Only include paths you are confident about"
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 3. LLM response parser
# ---------------------------------------------------------------------------

def parse_llm_response(raw: str) -> TaintAnalysisResult:
    """Parse LLM JSON response into TaintAnalysisResult.

    On any parsing error, returns an empty TaintAnalysisResult (conservative).
    """
    try:
        data = json.loads(raw)
        return TaintAnalysisResult.model_validate(data)
    except (json.JSONDecodeError, Exception) as exc:
        logger.debug("Failed to parse LLM response: %s", exc)
        return TaintAnalysisResult(tainted_params=[], propagation_paths=[])


# ---------------------------------------------------------------------------
# 4. IntraResult conversion
# ---------------------------------------------------------------------------

def _intra_result_from_llm(
    block: FuncBlock,
    llm_result: TaintAnalysisResult,
    sinks_in_func: list[SinkCallSite],
) -> IntraResult:
    """Convert TaintAnalysisResult to IntraResult.

    Validates tainted_params against block.parameters and sink_ids against
    known sinks. Preserves sanitizer info (sanitized/sanitizer_description/
    intermediate_vars/post_sanitized_concat) into local_steps as summary
    PropagationStep — 之前硬编码 local_steps=[] 导致 sanitizer 管道断链。
    """
    valid_params = set(block.parameters)
    valid_sink_ids = {s.id for s in sinks_in_func}
    sink_line_map = {s.id: s.line for s in sinks_in_func}

    tainted = {p for p in llm_result.tainted_params if p in valid_params}

    hits: dict[str, float] = {}
    local_steps: list[PropagationStep] = []
    for path in llm_result.propagation_paths:
        if path.sink_id not in valid_sink_ids or path.source_param not in valid_params:
            continue
        existing = hits.get(path.sink_id, 0.0)
        hits[path.sink_id] = max(existing, path.confidence)

        # summary step:函数内 param→sink 路径,transformation 编码 sanitizer + post_concat
        tf: str | None = None
        if path.sanitized:
            desc = path.sanitizer_description or "unknown"
            tf = f"sanitize_hint:{desc}"
            if path.post_sanitized_concat:
                tf += "|post_concat"
        local_steps.append(PropagationStep(
            from_func_id=block.id,
            from_param=path.source_param,
            to_func_id=block.id,            # sink 在本函数内
            to_param=path.sink_id,
            transformation=tf,
            code_location=f"{block.file_path}:{sink_line_map.get(path.sink_id, block.start_line)}",
            intermediate_vars=list(path.intermediate_vars),
            confidence=path.confidence,
        ))

    return IntraResult(
        tainted_params=tainted,
        hits=hits,
        local_steps=local_steps,
    )


# ---------------------------------------------------------------------------
# 4b. Deterministic intra fallback helpers (spec 改动: 立场 B)
# ---------------------------------------------------------------------------

def _is_literal_expression(expr: str) -> bool:
    """保守判断 expression 是否为字面量常量(明确非注入源)。

    仅认明确字面量形态(引号字符串 / 数字 / 布尔 / null / 空);任何变量、
    属性访问、表达式返回 False(留给 is_entry_hint / LLM 判断)。
    """
    e = expr.strip()
    if not e:
        return True
    # 引号字符串 — 排除拼接表达式(首尾恰好同引号字符,如 "X" + col + "Y"
    # 或变参槽 'a','b');含拼接操作符的不是单个字面量。
    if (len(e) >= 2 and e[0] in "\"'" and e[-1] == e[0]
            and "+" not in e and "," not in e):
        return True
    # 数字(整数 / 浮点,含正负号)
    cleaned = e.lstrip("+-")
    if cleaned.isdigit():
        return True
    if cleaned.count(".") == 1 and cleaned.replace(".", "", 1).isdigit():
        return True
    # 布尔 / 空常量
    return e in {"true", "false", "null", "None", "True", "False"}


# 置信度分层(spec §3.2):直达参数→sink 用 AST 浅判断(is_entry_hint)确认;
# 间接/未跟踪流给低置信,留给 LLM 或 LLM vuln 轨复核。
_DIRECT_HIT_CONFIDENCE = 0.9
_INDIRECT_HIT_CONFIDENCE = 0.5


def _deterministic_intra_fallback(
    block: FuncBlock,
    sinks_in_func: list[SinkCallSite],
) -> IntraResult:
    """LLM 不可用时的确定性 intra 判断(spec 改动: 立场 B)。

    用 SinkCallSite.dangerous_slots[].is_entry_hint(AST 浅判断)给 sink 命中分层,
    并过滤纯字面量 sink:
      - 任一 slot is_entry_hint=True  → hits[sink.id] = 0.9(直达)
      - 否则若全部 slot 为字面量      → 不进 hits(过滤常量 sink,降噪)
      - 否则(变量引用,非直达)       → hits[sink.id] = 0.5(间接,需复核)

    tainted_params 保守保留全部参数 —— 保 propagate_across_chains 的 chain seed
    与跨函数传播,不损失召回(双轨铁律:GitNexus 轨确定性补召回)。
    """
    hits: dict[str, float] = {}
    for sink in sinks_in_func:
        slots = sink.dangerous_slots
        if any(slot.is_entry_hint for slot in slots):
            hits[sink.id] = _DIRECT_HIT_CONFIDENCE
            continue
        if slots and all(_is_literal_expression(slot.expression) for slot in slots):
            continue  # 纯字面量 sink: 明确非注入源,过滤
        hits[sink.id] = _INDIRECT_HIT_CONFIDENCE  # 间接 / 未跟踪
    return IntraResult(
        tainted_params=set(block.parameters),
        hits=hits,
        local_steps=[],
    )


# ---------------------------------------------------------------------------
# 5. Main entry point
# ---------------------------------------------------------------------------

async def analyze_taint_llm(
    block: FuncBlock,
    sinks_in_func: list[SinkCallSite],
    *,
    typed_params: list[TypedParameter] | None = None,
    llm_client: LLMClient | None = None,
    retry_count: int = 1,
) -> IntraResult:
    """Analyze taint propagation within a single function using LLM.

    Args:
        block: The function to analyze.
        sinks_in_func: Detected sinks within this function.
        typed_params: Parameter type/source info (optional).
        llm_client: Async callable (prompt, **kwargs) -> raw JSON string.
        retry_count: Number of retries on LLM failure.

    Returns:
        IntraResult with tainted params and sink hit confidences.
        On failure, conservatively marks all params as tainted.
    """
    # Fast path: no params → nothing to taint
    if not block.parameters:
        return IntraResult(tainted_params=set(), hits={}, local_steps=[])

    prompt = build_taint_prompt(block, sinks_in_func, typed_params=typed_params)

    # Call LLM with retries
    raw_response: str | None = None
    last_exc: Exception | None = None

    if llm_client is not None:
        for attempt in range(retry_count + 1):
            try:
                raw_response = await llm_client(prompt)
                break
            except Exception as exc:
                last_exc = exc
                logger.debug(
                    "LLM call attempt %d/%d failed: %s",
                    attempt + 1, retry_count + 1, exc,
                )

    # Parse response
    if raw_response is not None:
        llm_result = parse_llm_response(raw_response)
        return _intra_result_from_llm(block, llm_result, sinks_in_func)

    # Deterministic fallback (spec 改动: 立场 B): use is_entry_hint to tier
    # sink hits and filter literal sinks, instead of bluntly marking all
    # params tainted and all sinks hit at 1.0. tainted_params stays
    # conservative (all params) to preserve propagate seed / cross-function
    # propagation — no recall loss.
    if llm_client is None:
        # 预期降级路径(SUPERNOVA_GITNEXUS_LLM_ENABLED=0 或未配置 LLM client):
        # 静默 fallback,不打 warning — 避免每个函数一条 "LLM taint analysis
        # failed" 刷屏(2026-07-01)。
        logger.debug(
            "analyze_taint_llm: no LLM client, using deterministic fallback for %s",
            block.id,
        )
    else:
        logger.warning(
            "LLM taint analysis failed for %s (last error: %s). "
            "Using deterministic fallback (is_entry_hint tiered hits).",
            block.id, last_exc,
        )
    return _deterministic_intra_fallback(block, sinks_in_func)
