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

from shannon_core.code_index.models import FuncBlock, TypedParameter
from shannon_core.code_index.parameter_models import (
    IntraResult,
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
    known sinks.
    """
    valid_params = set(block.parameters)
    valid_sink_ids = {s.id for s in sinks_in_func}

    # Filter tainted_params to only known parameters
    tainted = {p for p in llm_result.tainted_params if p in valid_params}

    # Build hits map from validated propagation paths
    hits: dict[str, float] = {}
    for path in llm_result.propagation_paths:
        if path.sink_id in valid_sink_ids and path.source_param in valid_params:
            # Keep highest confidence for each sink
            existing = hits.get(path.sink_id, 0.0)
            hits[path.sink_id] = max(existing, path.confidence)

    return IntraResult(
        tainted_params=tainted,
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

    # Conservative fallback: mark all params as tainted, no hits
    logger.warning(
        "LLM taint analysis failed for %s (last error: %s). "
        "Using conservative fallback: all params tainted.",
        block.id, last_exc,
    )
    return IntraResult(
        tainted_params=set(block.parameters),
        hits={},
        local_steps=[],
    )
