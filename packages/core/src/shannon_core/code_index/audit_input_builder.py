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

from shannon_core.code_index.models import CallChain, CodeIndex, FuncBlock
from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    ParameterPropagationGraph,
    SinkCallSite,
    TaintFlow,
)
from shannon_core.code_index.tiered_audit import AuditPlan

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


# === Spec C: static dataflow hints (consumption-side) ===

_TIER_TITLES = {
    3: "Tier 3（高风险链）",
    2: "Tier 2（中风险链）",
    1: "Tier 1（低风险链）",
    0: "未归类（无 chain 关联）",
}


def _func_id_to_tier(index: CodeIndex, audit_plan: AuditPlan) -> dict[str, int]:
    """Map each FuncBlock.id to the highest-priority tier of any chain whose
    path contains it. Drives tier-sorted sink inventory (Spec §4.4).

    AuditPlan stores ChainRiskScore (chain_id = '→'.join(path[:4])), so we
    re-derive the same key from index.chains to look up each chain's tier.
    A func that sits on both a tier3 and a tier1 chain keeps tier3 (max).
    """
    tier_by_chain_key = {score.chain_id: score.tier for score in audit_plan.scores}
    func_to_tier: dict[str, int] = {}
    for chain in index.chains:
        key = "→".join(chain.path[:4])
        tier = tier_by_chain_key.get(key)
        if tier is None:
            continue
        for func_id in chain.path:
            prev = func_to_tier.get(func_id, 0)
            if tier > prev:
                func_to_tier[func_id] = tier
    return func_to_tier


def _header(language_coverage: list[str], skipped_languages: list[str]) -> str:
    covered = ", ".join(language_coverage) if language_coverage else "（无）"
    skipped = ", ".join(skipped_languages) if skipped_languages else "无"
    return (
        "# Static Dataflow Hints（确定性静态线索，需 LLM 验证）\n\n"
        "## 覆盖范围\n"
        f"- 已静态分析语言：{covered}\n"
        f"- 未覆盖语言（无静态污点线索，请自行追链）：{skipped}\n"
        "- ⚠️ 本文件是【线索】非【结论】。静态未列出的 sink/路径不代表安全。"
    )


def _format_callee(site: SinkCallSite) -> str:
    if site.callee_receiver:
        return f"{site.callee_receiver}.{site.callee_name}"
    return site.callee_name


def _format_slots(slots: list[DangerousSlot]) -> str:
    """Render dangerous slots as '(arg_index, slot_value); ...'."""
    parts = [f"({s.arg_index}, {s.slot.value})" for s in slots]
    return "; ".join(parts) if parts else "—"


def _sink_inventory(
    sink_call_sites: list[SinkCallSite],
    func_to_tier: dict[str, int],
) -> str:
    """Render sink call sites grouped by audit tier (tier3 first)."""
    if not sink_call_sites:
        return "## Sink 调用点\n（本仓库无静态命中的 sink 调用点。）"

    buckets: dict[int, list[SinkCallSite]] = {3: [], 2: [], 1: [], 0: []}
    for site in sink_call_sites:
        tier = func_to_tier.get(site.caller_id, 0)
        buckets.setdefault(tier, []).append(site)

    lines = ["## Sink 调用点（按审计优先级）"]
    for tier in (3, 2, 1, 0):
        sites = buckets.get(tier, [])
        if not sites:
            continue
        lines.append(f"### {_TIER_TITLES[tier]}")
        for s in sites:
            review = " · ⚠️needs_review" if s.needs_review else ""
            lines.append(
                f"- `{s.id}` ({s.file_path}:{s.line}:{s.column}) "
                f"{s.category.value}/{s.sink_subtype} @ `{_format_callee(s)}` "
                f"· 危险槽: {_format_slots(s.dangerous_slots)} · rule={s.rule_id}"
                f"{review}"
            )
    return "\n".join(lines)
