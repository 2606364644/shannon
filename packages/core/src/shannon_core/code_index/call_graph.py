import logging
from collections import defaultdict

from shannon_core.code_index.models import CallChain, CallEdge, FuncBlock

logger = logging.getLogger(__name__)


def resolve_edges(edges: list[CallEdge], blocks: list[FuncBlock]) -> list[CallEdge]:
    """Resolve call edges by matching callee names to known function blocks."""
    name_index: dict[str, list[FuncBlock]] = defaultdict(list)
    for block in blocks:
        name_index[block.function_name].append(block)

    resolved: list[CallEdge] = []
    for edge in edges:
        candidates = name_index.get(edge.callee_name, [])
        if candidates:
            match = candidates[0]
            resolved.append(CallEdge(
                caller_id=edge.caller_id,
                callee_name=edge.callee_name,
                callee_file=match.file_path,
                resolved=True,
                line=edge.line,
            ))
        else:
            resolved.append(edge)
    return resolved


def build_call_chains(
    entry_point_ids: list[str],
    edges: list[CallEdge],
    max_depth: int = 15,
    max_width: int = 50,
    blocks: list[FuncBlock] | None = None,
) -> list[CallChain]:
    """Build call chains from entry points using BFS."""
    # Build a lookup from (file, name) to full FuncBlock ID (includes line number)
    block_lookup: dict[tuple[str, str], str] = {}
    if blocks:
        for block in blocks:
            block_lookup[(block.file_path, block.function_name)] = block.id

    adj: dict[str, list[CallEdge]] = defaultdict(list)
    for edge in edges:
        adj[edge.caller_id].append(edge)

    chains: list[CallChain] = []

    for ep_id in entry_point_ids:
        queue: list[tuple[list[str], int, bool]] = [([ep_id], 0, False)]

        while queue:
            path, depth, has_unresolved = queue.pop(0)
            current_id = path[-1]

            outgoing = adj.get(current_id, [])
            resolved_outgoing = [e for e in outgoing if e.resolved][:max_width]
            unresolved_outgoing = [e for e in outgoing if not e.resolved]

            if not resolved_outgoing:
                chain_unresolved = has_unresolved or len(unresolved_outgoing) > 0
                chains.append(CallChain(
                    entry_point_id=ep_id,
                    path=path,
                    depth=depth,
                    has_unresolved=chain_unresolved,
                ))
                continue

            if depth >= max_depth:
                chains.append(CallChain(
                    entry_point_id=ep_id,
                    path=path,
                    depth=depth,
                    has_unresolved=True,
                ))
                continue

            for edge in resolved_outgoing:
                # Use the full block ID (file:name:line) if blocks were provided,
                # otherwise fall back to file:name
                if blocks and edge.callee_file:
                    callee_id = block_lookup.get(
                        (edge.callee_file, edge.callee_name),
                        f"{edge.callee_file}:{edge.callee_name}",
                    )
                elif edge.callee_file:
                    callee_id = f"{edge.callee_file}:{edge.callee_name}"
                else:
                    callee_id = edge.callee_name

                if callee_id in path:
                    # Cycle detected: emit the chain without the duplicate node
                    chains.append(CallChain(
                        entry_point_id=ep_id,
                        path=path,
                        depth=depth,
                        has_unresolved=True,
                    ))
                    continue

                new_unresolved = has_unresolved or len(unresolved_outgoing) > 0
                queue.append((path + [callee_id], depth + 1, new_unresolved))

    return chains
