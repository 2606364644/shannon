"""GitNexus MCP call graph builder.

Replaces the old AST-based call_graph.py with precise call relationships
obtained via GitNexus MCP tools (query + process).

``build_call_graph_from_gitnexus`` consumes GitNexus **process trace**
resources (entry→terminal chains pre-computed at index time) instead of
the old full cypher CALLS-edge dump + Python BFS reconstruction (which
readline-crashed and produced empty chains in production).
"""
import logging
from pathlib import Path

from supernova_core.code_index.models import (
    CallChain,
    CallEdge,
    CallGraphResult,
    DegradationReport,
    FuncBlock,
    GitNexusNotIndexedError,
)
from supernova_core.code_index.process_trace_reader import (
    read_all_process_traces,
    trace_to_chain,
)

logger = logging.getLogger(__name__)


async def build_call_graph_from_gitnexus(
    repo_path: str,
    mcp_client: "object",
    blocks: list[FuncBlock],
) -> CallGraphResult:
    """Build a call graph from GitNexus process trace resources.

    process trace = GitNexus 索引时预计算的 entry→terminal 调用链。替代旧的
    「全量 cypher CALLS 边 + Python BFS 重建」——后者 readline 崩、空壳、不通用。

    流程：
      1. cypher probe（MATCH Process）→ None 表示未索引 → raise
      2. read_all_process_traces → ProcessTrace[]
      3. trace_to_chain 每条 → CallChain[]
      4. entry_points = 各 chain path[0] 对应 FuncBlock（去重）；edges=[]（废弃）

    Raises GitNexusNotIndexedError if the cypher probe returns ``None``
    (repo not indexed).
    """
    probe = await mcp_client.call_tool(
        "cypher",
        {"query": "MATCH (p:Process) RETURN p.label AS label"},
    )

    if probe is None:
        raise GitNexusNotIndexedError(
            f"GitNexus has not indexed repository: {repo_path}"
        )

    repo_name = Path(repo_path).name
    traces = await read_all_process_traces(mcp_client, repo_name)

    chains: list[CallChain] = []
    for trace in traces:
        chain = trace_to_chain(trace, blocks)
        if chain and chain.path:
            chains.append(chain)

    block_by_id: dict[str, FuncBlock] = {b.id: b for b in blocks}
    entry_blocks: list[FuncBlock] = []
    seen: set[str] = set()
    for ch in chains:
        head = block_by_id.get(ch.entry_point_id)
        if head and head.id not in seen:
            entry_blocks.append(head)
            seen.add(head.id)

    logger.info(
        "build_call_graph_from_gitnexus: %d traces → %d chains, %d entries",
        len(traces), len(chains), len(entry_blocks),
    )

    return CallGraphResult(
        edges=[],
        chains=chains,
        entry_points=entry_blocks,
        degradation_report=DegradationReport(
            total_edges=0, resolved_count=0, unresolved_count=0,
        ),
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
