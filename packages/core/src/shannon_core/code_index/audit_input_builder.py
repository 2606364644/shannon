"""Audit input builder — formats call chain data for audit agent prompts.

Builds the structured text input that audit agents receive:
- Complete source code for each function in the chain
- Taint flow summary (parameter propagation)
- Sink locations and types

Two formats:
- build_chain_audit_input(): Full format for Tier 2/3 agents
- build_tier1_audit_input(): Compact format for Tier 1 combined agent
"""

import logging

from shannon_core.code_index.models import CallChain, FuncBlock
from shannon_core.code_index.parameter_models import TaintFlow

logger = logging.getLogger(__name__)


def build_chain_audit_input(
    chain: CallChain,
    blocks_by_id: dict[str, FuncBlock],
    taint_flows: list[TaintFlow],
) -> str:
    """Build full audit input for a Tier 2/3 agent.

    Includes complete source code for each function in the chain,
    the taint flow summary, and sink locations.
    """
    sections: list[str] = []

    # Call chain header
    chain_summary = " → ".join(
        blocks_by_id[nid].function_name if nid in blocks_by_id else nid.split(":")[1]
        for nid in chain.path
    )
    sections.append(f"## Call Chain\n{chain_summary}\n")

    # Source code for each function
    for i, func_id in enumerate(chain.path):
        block = blocks_by_id.get(func_id)
        if block:
            lang = block.language
            sections.append(
                f"## Function {i + 1}: {block.function_name} "
                f"({block.file_path}:{block.start_line}-{block.end_line})\n"
                f"```{lang}\n{block.source_code}\n```\n"
            )

    # Taint flow
    sections.append(f"## Taint Flow (from Parameter Propagation Graph)\n"
                    f"{format_taint_flow_summary(taint_flows)}\n")

    # Sinks — Spec A 升级
    sinks = []
    for flow in taint_flows:
        if flow.sink_call_site_id:
            slot = flow.sink_slot.value if flow.sink_slot else "generic"
            sanitizer = " [sanitizer_hint]" if flow.has_sanitizer_hint else ""
            sinks.append(
                f"- {slot} sink at {flow.sink_call_site_id} "
                f"(arg {flow.tainted_arg_index}, conf={flow.confidence:.2f})"
                f"{sanitizer}"
            )
        elif flow.sink_type and flow.sink_func_id:
            # 旧字段回退
            sink_label = flow.sink_type.value.replace("_", " ")
            # Capitalize first word only (e.g. "sql execution" → "SQL execution")
            first_space = sink_label.find(" ")
            if first_space >= 0:
                sink_label = sink_label[:first_space].upper() + sink_label[first_space:]
            else:
                sink_label = sink_label.upper()
            sinks.append(f"- {sink_label} sink at {flow.sink_func_id}")
    if sinks:
        sections.append("## Sinks in this chain\n" + "\n".join(sinks) + "\n")
    else:
        sections.append("## Sinks in this chain\nNo identified sinks.\n")

    return "\n".join(sections)


def build_tier1_audit_input(
    chain: CallChain,
    blocks_by_id: dict[str, FuncBlock],
    taint_flows: list[TaintFlow],
) -> str:
    """Build compact audit input for Tier 1 combined agent."""
    chain_summary = " → ".join(
        blocks_by_id[nid].function_name if nid in blocks_by_id else nid.split(":")[1]
        for nid in chain.path
    )

    parts = [
        "## Quick Security Scan\n",
        f"Analyze this call chain for ALL vulnerability types at a high level.\n",
        f"## Call Chain: {chain_summary}\n",
    ]

    # Source code (same as full format but shorter preamble)
    for i, func_id in enumerate(chain.path):
        block = blocks_by_id.get(func_id)
        if block:
            parts.append(
                f"## Function {i + 1}: {block.function_name} "
                f"({block.file_path}:{block.start_line})\n"
                f"```{block.language}\n{block.source_code}\n```\n"
            )

    parts.append(f"## Taint Flow: {format_taint_flow_summary(taint_flows)}\n")

    return "\n".join(parts)


def format_taint_flow_summary(flows: list[TaintFlow]) -> str:
    """Format taint flows as a human-readable summary.

    Shows the propagation path from source parameter to sink.
    """
    if not flows:
        return "No taint flow data available for this chain."

    lines: list[str] = []
    for flow in flows:
        source_label = f"{flow.source_param} ({flow.source_type.value})"
        if flow.propagation_steps:
            path_parts = [source_label]
            for step in flow.propagation_steps:
                transform = f" [{step.transformation}]" if step.transformation else ""
                path_parts.append(f"{step.to_param}{transform}")
            if flow.sink_call_site_id:
                slot = flow.sink_slot.value if flow.sink_slot else "generic"
                sanitizer = " (sanitizer_hint)" if flow.has_sanitizer_hint else ""
                tail = f"{slot}@arg{flow.tainted_arg_index}{sanitizer}"
            else:
                tail = flow.sink_type.value if flow.sink_type else "unknown"
            lines.append(f"- {flow.source_type.value}: {' → '.join(path_parts)} → {tail}")
        else:
            lines.append(f"- {source_label} (no propagation steps)")

    return "\n".join(lines)
