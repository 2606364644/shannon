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

    Uses the real GitNexus MCP API:
    1. ``query({query: "entry point"})`` — process-grouped search returning
       ``processes``, ``process_symbols``, and ``definitions``.
    2. For each symbol, ``context({name: "…"})`` — 360° view with
       ``incoming.calls`` and ``outgoing.calls`` that become CallEdges.
    3. ``cypher`` — raw graph query to discover CALLS relationships and
       confidence scores for all edges.

    Raises GitNexusNotIndexedError if query returns None (repo not indexed).
    """
    # ── Step 1: query for entry-point candidates ──────────────────────
    query_result = await mcp_client.call_tool(
        "query",
        {"query": "entry point"},
    )

    if query_result is None:
        raise GitNexusNotIndexedError(
            f"GitNexus has not indexed repository: {repo_path}"
        )

    # ── Step 2: extract symbols and match to tree-sitter blocks ───────
    # query returns: {"processes": [...], "process_symbols": [...], "definitions": [...]}
    process_symbols: list[dict] = query_result.get("process_symbols", []) if isinstance(query_result, dict) else []
    definitions: list[dict] = query_result.get("definitions", []) if isinstance(query_result, dict) else []

    # Build block index for matching  (file_path, func_name, start_line) → FuncBlock
    block_index: dict[tuple[str, str, int], FuncBlock] = {}
    for block in blocks:
        block_index[(block.file_path, block.function_name, block.start_line)] = block
    # Also index by (file_path, func_name) for fuzzy matching
    block_by_name: dict[tuple[str, str], FuncBlock] = {}
    for block in blocks:
        block_by_name.setdefault((block.file_path, block.function_name), block)

    entry_point_blocks: list[FuncBlock] = []
    entry_point_ids: list[str] = []

    all_symbols = process_symbols + definitions
    seen_block_ids: set[str] = set()
    for sym in all_symbols:
        if not isinstance(sym, dict):
            continue
        file_path = sym.get("filePath", "")
        name = sym.get("name", "")
        # Try exact match first (file + name + line), then fuzzy (file + name)
        line = sym.get("startLine") or sym.get("line", 0)
        if isinstance(line, str):
            try:
                line = int(line)
            except (ValueError, TypeError):
                line = 0
        matched = block_index.get((file_path, name, line))
        if not matched:
            matched = block_by_name.get((file_path, name))
        if matched and matched.id not in seen_block_ids:
            entry_point_blocks.append(matched)
            entry_point_ids.append(matched.id)
            seen_block_ids.add(matched.id)

    # ── Step 3: get call edges via cypher query ───────────────────────
    edges: list[CallEdge] = []
    try:
        cypher_result = await mcp_client.call_tool(
            "cypher",
            {"query": "MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(callee) RETURN caller.filePath AS caller_file, caller.name AS caller_name, caller.startLine AS caller_line, callee.filePath AS callee_file, callee.name AS callee_name, r.confidence AS confidence LIMIT 5000"},
        )
        if isinstance(cypher_result, list):
            for record in cypher_result:
                if not isinstance(record, dict):
                    continue
                caller_name = record.get("caller_name")
                callee_name = record.get("callee_name")
                if not caller_name or not callee_name:
                    continue
                caller_file = record.get("caller_file", "")
                caller_line = record.get("caller_line", 0) or 0
                callee_file = record.get("callee_file")
                if isinstance(caller_line, str):
                    try:
                        caller_line = int(caller_line)
                    except (ValueError, TypeError):
                        caller_line = 0
                caller_id = f"{caller_file}:{caller_name}:{caller_line}" if caller_file else caller_name
                resolved = callee_file is not None
                edges.append(CallEdge(
                    caller_id=caller_id,
                    callee_name=callee_name,
                    callee_file=callee_file,
                    resolved=resolved,
                    line=caller_line,
                ))
    except Exception as exc:
        logger.warning("Cypher query for call edges failed (%s); edge list will be empty", exc)

    # ── Step 4: build chains from edges ───────────────────────────────
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


def _build_upstream_chains(
    edges: list[CallEdge],
    sink_id: str,
) -> list[CallChain]:
    """Build CallChain list from upstream impact edges (callers → sink).

    Simple approach: one chain per unique caller, path = [caller_id, sink_id].
    """
    if not edges:
        return []

    chains: list[CallChain] = []
    seen_callers: set[str] = set()
    for edge in edges:
        if edge.caller_id not in seen_callers:
            seen_callers.add(edge.caller_id)
            chains.append(CallChain(
                entry_point_id=edge.caller_id,
                path=[edge.caller_id, sink_id],
                depth=1,
                has_unresolved=not edge.resolved,
            ))

    return chains


async def trace_from_sink(
    mcp_client: "object",
    sink_name: str,
    sink_file: str,
    sink_line: int,
    *,
    direction: str = "upstream",
    max_depth: int = 5,
) -> CallGraphResult:
    """Trace upstream callers from a sink function using the impact MCP tool.

    Calls ``impact`` with the given target and direction, then parses the
    returned upstream/downstream entries into CallEdge objects and builds
    chains.

    Returns an empty ``CallGraphResult`` when the impact response is ``None``
    or a plain string.
    """
    impact_result = await mcp_client.call_tool(
        "impact",
        {
            "target": sink_name,
            "direction": direction,
            "maxDepth": max_depth,
        },
    )

    # Guard against None or string responses
    if impact_result is None or isinstance(impact_result, str):
        return CallGraphResult(
            edges=[],
            chains=[],
            entry_points=[],
            degradation_report=DegradationReport(),
        )

    sink_id = f"{sink_file}:{sink_name}:{sink_line}"
    entries = impact_result.get(direction, []) or []
    edges: list[CallEdge] = []

    for entry in entries:
        name = entry.get("name")
        if not name:
            continue
        file = entry.get("file")
        line = entry.get("line", 0)

        if direction == "upstream":
            # upstream: caller -> sink
            caller_id = f"{file}:{name}:{line}" if file else name
            edges.append(CallEdge(
                caller_id=caller_id,
                callee_name=sink_name,
                callee_file=sink_file,
                resolved=file is not None,
                line=line,
            ))
        else:
            # downstream: sink -> callee
            edges.append(CallEdge(
                caller_id=sink_id,
                callee_name=name,
                callee_file=file,
                resolved=file is not None,
                line=line,
            ))

    chains = _build_upstream_chains(edges, sink_id)

    resolved_count = sum(1 for e in edges if e.resolved)
    degradation_report = DegradationReport(
        total_edges=len(edges),
        resolved_count=resolved_count,
        unresolved_count=len(edges) - resolved_count,
    )

    return CallGraphResult(
        edges=edges,
        chains=chains,
        entry_points=[],
        degradation_report=degradation_report,
    )


async def find_sinks_by_patterns(
    mcp_client: "object",
    patterns: list[str],
) -> list[dict]:
    """Discover sink functions by querying GitNexus for each pattern.

    Deduplicates results by name.  Returns a list of dicts with keys
    ``name``, ``filePath``, ``startLine``.
    """
    seen_names: set[str] = set()
    sinks: list[dict] = []

    for pattern in patterns:
        result = await mcp_client.call_tool("query", {"query": pattern})
        if result is None:
            continue
        for entry in result:
            name = entry.get("name")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            sinks.append({
                "name": name,
                "filePath": entry.get("filePath", ""),
                "startLine": entry.get("startLine", 0),
            })

    return sinks


async def get_function_context(
    mcp_client: "object",
    function_name: str,
) -> dict | None:
    """Retrieve symbol details for a function via the context MCP tool.

    Returns the raw dict response from the tool, or ``None`` if the tool
    returns ``None``.
    """
    result = await mcp_client.call_tool("context", {"name": function_name})
    if result is None:
        return None
    return result
