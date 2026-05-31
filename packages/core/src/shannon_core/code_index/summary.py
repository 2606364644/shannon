from shannon_core.code_index.models import CodeIndex


def generate_summary(index: CodeIndex) -> str:
    """Generate a human/LLM-readable markdown summary from a CodeIndex."""
    lines: list[str] = []

    lines.append(f"# Code Index Summary: {index.repository}")
    lines.append("")
    lines.append(f"**Language:** {index.language}")
    lines.append(f"**Total Function Blocks:** {index.total_blocks}")
    lines.append(f"**Total Entry Points:** {index.total_entry_points}")
    lines.append(f"**Total Call Chains:** {index.total_chains}")
    lines.append("")

    # --- Entry Points Table ---
    lines.append("## Entry Points")
    lines.append("")

    if index.entry_points:
        lines.append("| Endpoint | Method | Function | File:Line | Confidence |")
        lines.append("|----------|--------|----------|-----------|------------|")
        for ep in index.entry_points:
            block = _find_block(index, ep.func_block_id)
            route = ep.route or "—"
            method = ep.http_method or "—"
            func_name = block.function_name if block else "—"
            location = f"{block.file_path}:{block.start_line}" if block else "—"
            lines.append(f"| {route} | {method} | {func_name} | {location} | {ep.confidence:.2f} |")
    else:
        lines.append("_No entry points detected._")
    lines.append("")

    # --- Entry Points Needing Review ---
    needs_review = [ep for ep in index.entry_points if ep.needs_llm_review]
    lines.append("## Entry Points Needing LLM Review")
    lines.append("")
    if needs_review:
        for ep in needs_review:
            block = _find_block(index, ep.func_block_id)
            lines.append(f"- **{block.function_name if block else ep.func_block_id}** "
                         f"(confidence: {ep.confidence:.2f})")
            lines.append(f"  - Evidence: {ep.evidence}")
            lines.append(f"  - Location: {block.file_path}:{block.start_line}" if block else "")
    else:
        lines.append("_All entry points have high confidence (> 0.8)._")
    lines.append("")

    # --- Coverage Metrics ---
    resolved_count = sum(1 for e in index.edges if e.resolved)
    unresolved_count = sum(1 for e in index.edges if not e.resolved)
    total_edges = len(index.edges)
    chains_with_unresolved = sum(1 for c in index.chains if c.has_unresolved)
    max_chain_depth = max((c.depth for c in index.chains), default=0)

    ep_ids = {ep.func_block_id for ep in index.entry_points}
    chain_block_ids: set[str] = set()
    for chain in index.chains:
        chain_block_ids.update(chain.path)
    unreachable = [b for b in index.blocks if b.id not in chain_block_ids and b.id not in ep_ids]

    lines.append("## Coverage Metrics")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total Call Edges | {total_edges} |")
    lines.append(f"| Resolved Edges | {resolved_count} |")
    lines.append(f"| Unresolved Edges | {unresolved_count} |")
    lines.append(f"| Max Chain Depth | {max_chain_depth} |")
    lines.append(f"| Chains with Unresolved Calls | {chains_with_unresolved} |")
    lines.append(f"| Unreachable Functions | {len(unreachable)} |")
    lines.append("")

    return "\n".join(lines)


def _find_block(index: CodeIndex, block_id: str):
    for block in index.blocks:
        if block.id == block_id:
            return block
    return None