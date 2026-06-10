"""GitNexus MCP call graph builder.

Replaces the old AST-based call_graph.py with precise call relationships
obtained via GitNexus MCP tools (query + process).
"""
import logging
from collections import defaultdict

from shannon_core.code_index.models import (
    CallChain,
    CallEdge,
    CallGraphResult,
    DegradationReport,
    FuncBlock,
    GitNexusNotIndexedError,
)

logger = logging.getLogger(__name__)


def _parse_process_response(process_data: list[dict]) -> list[CallEdge]:
    """Parse GitNexus process tool response into CallEdge list.

    Each record has "caller" and "callee" dicts with "file", "name", "line".
    Skip records where caller or callee missing "name".
    Set resolved=False if callee missing "file".
    """
    edges: list[CallEdge] = []
    for record in process_data:
        caller = record.get("caller", {})
        callee = record.get("callee", {})

        caller_name = caller.get("name")
        callee_name = callee.get("name")

        # Skip records where caller or callee missing "name"
        if not caller_name or not callee_name:
            continue

        caller_file = caller.get("file", "")
        caller_line = caller.get("line", 0)
        callee_file = callee.get("file")

        caller_id = f"{caller_file}:{caller_name}:{caller_line}" if caller_file else caller_name

        resolved = callee_file is not None

        edges.append(CallEdge(
            caller_id=caller_id,
            callee_name=callee_name,
            callee_file=callee_file,
            resolved=resolved,
            line=caller_line,
        ))

    return edges


def _build_chains_from_edges(
    edges: list[CallEdge],
    entry_point_ids: list[str],
    blocks: list[FuncBlock] | None = None,
    max_depth: int = 20,
) -> list[CallChain]:
    """BFS from entry points through edges to build CallChain list.

    Build adjacency list from resolved edges. Uses block IDs for caller
    and callee matching when blocks are provided. Track visited paths to
    avoid duplicates. Handle leaf nodes and single-node chains.
    Cycle detection.
    """
    # Build lookups from (file, name) to FuncBlock ID
    block_by_name: dict[tuple[str, str], FuncBlock] = {}
    if blocks:
        for block in blocks:
            block_by_name[(block.file_path, block.function_name)] = block

    # Build adjacency list using block IDs when available
    # adj maps block_id -> list of (callee_block_id, is_unresolved_step)
    adj: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    for edge in edges:
        if edge.resolved and edge.callee_file:
            # Resolve caller_id to block ID
            caller_block_id = _resolve_caller_to_block_id(edge.caller_id, block_by_name)

            # Resolve callee to block ID
            callee_block = block_by_name.get((edge.callee_file, edge.callee_name))
            callee_id = callee_block.id if callee_block else f"{edge.callee_file}:{edge.callee_name}"

            adj[caller_block_id].append((callee_id, False))
        elif not edge.resolved:
            caller_block_id = _resolve_caller_to_block_id(edge.caller_id, block_by_name)
            adj[caller_block_id]  # ensure key exists for unresolved tracking

    # Track nodes with unresolved outgoing edges
    unresolved_outgoing: set[str] = set()
    for edge in edges:
        if not edge.resolved:
            caller_block_id = _resolve_caller_to_block_id(edge.caller_id, block_by_name)
            unresolved_outgoing.add(caller_block_id)

    chains: list[CallChain] = []

    for ep_id in entry_point_ids:
        # BFS: (path, depth, has_unresolved)
        queue: list[tuple[list[str], int, bool]] = [([ep_id], 0, False)]

        while queue:
            path, depth, has_unresolved = queue.pop(0)
            current_id = path[-1]

            outgoing = adj.get(current_id, [])

            if not outgoing:
                # Leaf node (no outgoing resolved edges)
                chain_unresolved = has_unresolved or current_id in unresolved_outgoing
                chains.append(CallChain(
                    entry_point_id=ep_id,
                    path=list(path),
                    depth=depth,
                    has_unresolved=chain_unresolved,
                ))
                continue

            if depth >= max_depth:
                chains.append(CallChain(
                    entry_point_id=ep_id,
                    path=list(path),
                    depth=depth,
                    has_unresolved=True,
                ))
                continue

            for callee_id, edge_unresolved in outgoing:
                # Cycle detection: check if callee already in current path
                if callee_id in path:
                    chains.append(CallChain(
                        entry_point_id=ep_id,
                        path=list(path),
                        depth=depth,
                        has_unresolved=True,
                    ))
                    continue

                new_unresolved = has_unresolved or edge_unresolved or current_id in unresolved_outgoing
                queue.append((path + [callee_id], depth + 1, new_unresolved))

    return chains


def _resolve_caller_to_block_id(
    caller_id: str,
    block_by_name: dict[tuple[str, str], FuncBlock],
) -> str:
    """Resolve a raw caller_id to a FuncBlock ID by matching (file, name)."""
    parts = caller_id.split(":")
    if len(parts) >= 2:
        file_path, func_name = parts[0], parts[1]
        matched = block_by_name.get((file_path, func_name))
        if matched:
            return matched.id
    return caller_id


async def build_call_graph_from_gitnexus(
    repo_path: str,
    mcp_client: "object",
    blocks: list[FuncBlock],
) -> CallGraphResult:
    """Build a call graph using GitNexus MCP tools.

    1. Call mcp_client.call_tool("query", {...}) to get entry points
    2. Match to blocks
    3. Call mcp_client.call_tool("process", {...}) to get call chains
    4. Parse edges, build chains, create degradation report

    Raises GitNexusNotIndexedError if query returns None.
    """
    # Step 1: Query entry points from GitNexus
    query_result = await mcp_client.call_tool(
        "query",
        {"repo_path": repo_path, "query_type": "entry_points"},
    )

    if query_result is None:
        raise GitNexusNotIndexedError(
            f"GitNexus has not indexed repository: {repo_path}"
        )

    # Step 2: Match query results to known blocks
    block_index: dict[tuple[str, str, int], FuncBlock] = {}
    for block in blocks:
        block_index[(block.file_path, block.function_name, block.start_line)] = block

    entry_point_blocks: list[FuncBlock] = []
    entry_point_ids: list[str] = []
    for entry in query_result:
        key = (entry.get("file", ""), entry.get("name", ""), entry.get("line", 0))
        matched = block_index.get(key)
        if matched:
            entry_point_blocks.append(matched)
            entry_point_ids.append(matched.id)

    # Step 3: Get call chains via process tool
    process_result = await mcp_client.call_tool(
        "process",
        {"repo_path": repo_path, "process_type": "call_chains"},
    )

    process_data = process_result if process_result else []

    # Step 4: Parse edges and build chains
    edges = _parse_process_response(process_data)
    chains = _build_chains_from_edges(edges, entry_point_ids, blocks=blocks)

    # Build degradation report
    resolved_count = sum(1 for e in edges if e.resolved)
    degradation_report = DegradationReport(
        total_edges=len(edges),
        resolved_count=resolved_count,
        unresolved_count=len(edges) - resolved_count,
    )

    return CallGraphResult(
        edges=edges,
        chains=chains,
        entry_points=entry_point_blocks,
        degradation_report=degradation_report,
    )
