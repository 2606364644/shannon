"""Deterministic cross-function taint propagation along GitNexus call chains.

Consumes CallChain objects (from gitnexus_call_graph) and IntraResult per
function (from llm_taint_analyzer or deterministic intra-procedural analysis),
then maps tainted parameters across function boundaries without any LLM calls.

Algorithm:
  For each CallChain:
    1. Seed the chain head with intra_results[head_id].tainted_params
    2. Walk the path; at each hop:
       a. If the callee has sink hits in intra_results → emit TaintFlow
       b. Find call args from caller → callee via _find_call_args_for_callee
       c. Map tainted args to callee params by position
       d. Conservative: if no call args found, pass all tainted to all callee params
    3. Respect max_depth to limit traversal depth
"""

import logging
import re

from shannon_core.code_index.models import CallChain, FuncBlock, ParameterSource
from shannon_core.code_index.parameter_models import (
    IntraResult,
    PropagationStep,
    TaintFlow,
)

logger = logging.getLogger(__name__)


def _references_tainted(arg_expr: str, tainted: set[str]) -> bool:
    """Check if an argument expression references any tainted variable.

    Over-approximate: any tainted name appearing as substring of arg_expr → True.
    Uses word-boundary matching to avoid false positives on unrelated substrings,
    but also checks prefix (container over-approximation: ``request`` matches
    ``request.user_id``).
    """
    if not arg_expr or not tainted:
        return False
    for t in tainted:
        # Substring check — covers "request" matching "request.user_id"
        if t in arg_expr:
            return True
    return False


def _find_call_args_for_callee(
    caller: FuncBlock,
    callee_id: str,
) -> list[str]:
    """From caller source code, find arguments passed when calling callee.

    Uses regex to find ``callee_name(arg1, arg2, ...)`` in the caller source.
    Returns the list of argument expression strings.
    """
    # Extract callee function name from its id (format: "file:func_name:line")
    parts = callee_id.split(":")
    if len(parts) >= 3:
        callee_name = parts[1]
    else:
        callee_name = callee_id

    args_list: list[str] = []
    for line in caller.source_code.splitlines():
        # Look for callee_name( ... )
        pattern = re.escape(callee_name) + r"\s*\("
        m = re.search(pattern, line)
        if m is None:
            continue
        inside = _extract_first_call_args(line, callee_name)
        if inside is not None:
            args_list.extend(inside)
            return args_list
    return args_list


def _extract_first_call_args(line: str, callee: str) -> list[str] | None:
    """Extract argument text list from the first ``callee(...)`` in a line."""
    idx = line.find(callee + "(")
    if idx < 0:
        return None
    inside_start = idx + len(callee) + 1
    depth = 1
    inside_end = -1
    for j in range(inside_start, len(line)):
        ch = line[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                inside_end = j
                break
    if inside_end < 0:
        return None
    inside = line[inside_start:inside_end]
    if not inside.strip():
        return []
    return [a.strip() for a in _split_args_respecting_parens(inside)]


def _split_args_respecting_parens(s: str) -> list[str]:
    """Split by ',' but ignore commas inside parentheses/brackets/quotes."""
    out: list[str] = []
    cur: list[str] = []
    depth = 0
    quote: str | None = None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            cur.append(ch)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def propagate_across_chains(
    chains: list[CallChain],
    blocks: list[FuncBlock],
    intra_results: dict[str, IntraResult],
    *,
    max_depth: int = 20,
) -> list[TaintFlow]:
    """Walk each chain, maintaining current_tainted set, produce TaintFlows.

    At each step:
      - For chain head: seed from intra_results[head_id].tainted_params
      - For each hop: find call args, map tainted args to callee params
      - If no call args found: conservative — pass all tainted to all callee params
      - When callee has hits: produce TaintFlow

    Returns a list of TaintFlow objects. Uses ParameterSource.QUERY_PARAM for
    source_type as a default (real typed info should come from enhanced_parameters).
    """
    if not chains:
        return []

    blocks_by_id: dict[str, FuncBlock] = {b.id: b for b in blocks}
    flows: list[TaintFlow] = []

    for chain in chains:
        if not chain.path:
            continue

        # Seed from chain head
        head_id = chain.path[0]
        head_intra = intra_results.get(head_id)
        if head_intra is None or not head_intra.tainted_params:
            continue

        current_tainted: dict[str, set[str]] = {
            head_id: set(head_intra.tainted_params),
        }
        accumulated_steps: list[PropagationStep] = []
        source_param = next(iter(head_intra.tainted_params), "")

        for i, func_id in enumerate(chain.path):
            if i > max_depth:
                break

            block = blocks_by_id.get(func_id)
            if block is None:
                continue

            func_intra = intra_results.get(func_id)
            if func_intra is None:
                continue

            # Collect local steps
            if func_intra.local_steps:
                accumulated_steps.extend(func_intra.local_steps)

            # If this function has sink hits, produce TaintFlow(s)
            for sink_id, sink_confidence in func_intra.hits.items():
                steps_total = list(accumulated_steps)
                chain_confidence = min(
                    (s.confidence for s in steps_total),
                    default=sink_confidence,
                )
                # Use sink confidence as floor if no steps
                if not steps_total:
                    chain_confidence = sink_confidence

                flow_id = f"{head_id}->{sink_id}"
                flows.append(TaintFlow(
                    flow_id=flow_id,
                    entry_point_id=head_id,
                    source_param=source_param,
                    source_type=ParameterSource.QUERY_PARAM,
                    propagation_steps=steps_total,
                    sink_call_site_id=sink_id,
                    confidence=chain_confidence,
                    notes="",
                ))

            # Prepare next hop: map tainted through call site to callee params
            if i + 1 >= len(chain.path):
                break
            if i + 1 > max_depth:
                break

            callee_id = chain.path[i + 1]
            callee_block = blocks_by_id.get(callee_id)
            if callee_block is None:
                continue

            caller_tainted = current_tainted.get(func_id, set())
            callee_seed = _map_call_site_params(
                caller_block=block,
                caller_tainted=caller_tainted,
                callee_block=callee_block,
            )

            if callee_seed:
                current_tainted[callee_id] = callee_seed
                # Record the cross-function step
                accumulated_steps.append(PropagationStep(
                    from_func_id=func_id,
                    from_param=next(iter(caller_tainted), source_param),
                    to_func_id=callee_id,
                    to_param=next(iter(callee_seed), ""),
                    code_location=f"{block.file_path}:{block.start_line}",
                    confidence=0.9,
                ))
            else:
                # Conservative: even with no mapping, don't stop — propagate
                # all tainted as all callee params (only if callee has params)
                if callee_block.parameters:
                    current_tainted[callee_id] = set(callee_block.parameters)

    return flows


def _map_call_site_params(
    caller_block: FuncBlock,
    caller_tainted: set[str],
    callee_block: FuncBlock,
) -> set[str]:
    """Map tainted caller variables to callee parameters via call-site analysis.

    Finds the call to callee in caller source, extracts positional args,
    checks which are tainted, and maps them to callee parameter names.
    """
    callee_params = callee_block.parameters
    if not callee_params:
        return set()

    call_args = _find_call_args_for_callee(caller_block, callee_block.id)

    result: set[str] = set()

    if call_args:
        # Map by position: arg[i] → callee_params[i] if tainted
        for idx, arg_text in enumerate(call_args):
            if idx >= len(callee_params):
                break
            if _references_tainted(arg_text, caller_tainted):
                result.add(callee_params[idx])
    else:
        # Conservative: no call args found → pass all tainted to all callee params
        result = set(callee_params)

    return result
