# packages/core/tests/code_index/test_authz_dominance.py
from shannon_core.code_index.models import (
    CallChain, CallEdge, CodeIndex, EntryPoint, FuncBlock,
)
from shannon_core.code_index.authz_gitnexus_track import (
    IDORCandidateChain,
    find_unguarded_sink_paths,
)


def _block(bid, source, name=None):
    file_path, func_name, line = bid.rsplit(":", 2)
    return FuncBlock(
        id=bid, file_path=file_path, function_name=name or func_name,
        start_line=int(line), end_line=int(line) + 5, source_code=source,
        parameters=[], language="typescript",
    )


def _idx(blocks, edges, chains, entry_points=None):
    return CodeIndex(
        repository="r", language="typescript", total_blocks=len(blocks),
        total_entry_points=len(entry_points or []), total_chains=len(chains),
        blocks=blocks, edges=edges, entry_points=entry_points or [],
        chains=chains,
    )


def _ep(handler_id, route, method="DELETE"):
    return EntryPoint(
        func_block_id=handler_id, entry_type="http_route", route=route,
        http_method=method, confidence=0.9, evidence="",
        needs_llm_review=False, authentication="required",
    )


def test_no_candidates_when_handler_has_ownership_guard():
    """Handler body has ORM ownership predicate → no unguarded sink path."""
    handler = _block(
        "u.js:update:10",
        "async function update(req){\n"
        "  const row = await db.user.findFirst({ where: { userId: req.user.id } });\n"
        "  await db.user.update(row);\n"
        "}",
    )
    sink = _block("db.js:update:1", "function update(){ model.save(); }")
    chain = CallChain(
        entry_point_id=handler.id, path=[handler.id, sink.id],
        depth=1, has_unresolved=False,
    )
    index = _idx([handler, sink], [], [chain], [_ep(handler.id, "/api/users/:id")])
    cands = find_unguarded_sink_paths(index)
    assert cands == []  # ownership guard present in handler


def test_candidate_when_no_ownership_guard_reaches_sink():
    """Handler reaches a side-effect sink (ORM update) with no ownership predicate."""
    handler = _block(
        "u.js:update:10",
        "async function update(req){ await repo.update(req.params.id, req.body); }",
    )
    sink = _block("repo.js:update:1", "function update(){ db.user.update(); }")
    chain = CallChain(
        entry_point_id=handler.id, path=[handler.id, sink.id],
        depth=1, has_unresolved=False,
    )
    index = _idx([handler, sink], [], [chain], [_ep(handler.id, "/api/Feedbacks/:id")])
    cands = find_unguarded_sink_paths(index)
    assert len(cands) == 1
    c = cands[0]
    assert c.handler_id == handler.id
    assert c.sink_id == sink.id
    assert c.guard_nodes_on_path == ()  # no guard


def test_candidate_dedup_by_endpoint_sink():
    """Two chains to the same sink from the same endpoint → one candidate."""
    handler = _block("h.js:f:1", "async function f(req){ await s(req.id); }")
    sink = _block("s.js:g:1", "function g(){ db.user.remove(); }")
    ch1 = CallChain(entry_point_id=handler.id, path=[handler.id, sink.id], depth=1, has_unresolved=False)
    ch2 = CallChain(entry_point_id=handler.id, path=[handler.id, "x.js:m:1", sink.id], depth=2, has_unresolved=False)
    index = _idx([handler, sink], [], [ch1, ch2], [_ep(handler.id, "/api/u/:id")])
    cands = find_unguarded_sink_paths(index)
    assert len(cands) == 1  # deduped by (endpoint, sink)


def test_chains_without_side_effect_sink_are_skipped():
    """Chain ending in a non-sink (e.g. logger) → not a candidate."""
    handler = _block("h.js:f:1", "function f(){ log('x'); }")
    leaf = _block("log.js:l:1", "function l(){ console.log(); }")  # not a side-effect sink
    chain = CallChain(entry_point_id=handler.id, path=[handler.id, leaf.id], depth=1, has_unresolved=False)
    index = _idx([handler, leaf], [], [chain], [_ep(handler.id, "/api/x")])
    assert find_unguarded_sink_paths(index) == []


def test_respects_max_paths_per_endpoint():
    """Cap candidate count per endpoint to bound the judge-LLM cost."""
    handler = _block("h.js:f:1", "function f(){ sink1(); }")
    sinks = [
        _block(f"s.js:g{i}:1", f"function g{i}(){{ db.user.update(); }}")
        for i in range(5)
    ]
    chains = [
        CallChain(entry_point_id=handler.id, path=[handler.id, s.id],
                  depth=1, has_unresolved=False)
        for s in sinks
    ]
    index = _idx([handler, *sinks], [], chains, [_ep(handler.id, "/api/u/:id")])
    cands = find_unguarded_sink_paths(index, max_paths_per_endpoint=2)
    assert len(cands) == 2


def test_empty_index_yields_no_candidates():
    index = _idx([], [], [], [])
    assert find_unguarded_sink_paths(index) == []
