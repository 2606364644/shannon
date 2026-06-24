# packages/core/tests/code_index/test_authz_render_candidates.py
from shannon_core.code_index.models import (
    CallChain, CodeIndex, EntryPoint, FuncBlock,
)
from shannon_core.code_index.authz_gitnexus_track import (
    FrameworkIDORCandidate,
    IDORCandidateChain,
    render_authz_gitnexus_candidates,
)


def _block(bid, source):
    fp, fn, ln = bid.rsplit(":", 2)
    return FuncBlock(id=bid, file_path=fp, function_name=fn, start_line=int(ln),
                     end_line=int(ln) + 3, source_code=source, parameters=[],
                     language="typescript")


def _index():
    handler = _block("u.js:update:10", "async function update(req){ await repo.update(req.params.id); }")
    sink = _block("repo.js:update:1", "function update(){ db.user.update(); }")
    return CodeIndex(repository="r", language="typescript", total_blocks=2,
                     total_entry_points=1, total_chains=1,
                     blocks=[handler, sink], edges=[],
                     entry_points=[EntryPoint(func_block_id="u.js:update:10",
                                              entry_type="http_route", route="/api/u/:id",
                                              http_method="PUT", confidence=0.9, evidence="",
                                              needs_llm_review=False, authentication="required")],
                     chains=[CallChain(entry_point_id="u.js:update:10",
                                       path=["u.js:update:10", "repo.js:update:1"],
                                       depth=1, has_unresolved=False)])


def test_render_empty_candidates_yields_notice():
    index = _index()
    out = render_authz_gitnexus_candidates([], [], index=index, entry_points=index.entry_points)
    assert "无" in out or "no" in out.lower()


def test_render_dominance_candidate_lists_endpoint_and_path():
    index = _index()
    cand = IDORCandidateChain(
        endpoint_id="u.js:update:10", handler_id="u.js:update:10",
        sink_id="repo.js:update:1",
        path=("u.js:update:10", "repo.js:update:1"), guard_nodes_on_path=(),
    )
    out = render_authz_gitnexus_candidates([cand], [], index=index, entry_points=index.entry_points)
    assert "PUT /api/u/:id" in out
    assert "u.js:update:10" in out
    assert "repo.js:update:1" in out
    assert "ownership" in out.lower() or "guard" in out.lower()


def test_render_framework_candidate_lists_method_path_and_indicator():
    index = _index()
    fw = FrameworkIDORCandidate(
        method="DELETE", path="/api/Feedbacks/:id", framework="finale-rest",
        model="Feedback",
        vulnerability_indicators=("No ownership check on finale resource operations",),
    )
    out = render_authz_gitnexus_candidates([], [fw], index=index, entry_points=index.entry_points)
    assert "DELETE /api/Feedbacks/:id" in out
    assert "finale-rest" in out
    assert "No ownership check" in out


def test_render_includes_verdict_directive():
    index = _index()
    cand = IDORCandidateChain(
        endpoint_id="u.js:update:10", handler_id="u.js:update:10",
        sink_id="repo.js:update:1",
        path=("u.js:update:10", "repo.js:update:1"), guard_nodes_on_path=(),
    )
    out = render_authz_gitnexus_candidates([cand], [], index=index, entry_points=index.entry_points)
    # judge directive present
    assert "verdict" in out.lower() or "判定" in out
    assert "vulnerable" in out.lower() or "safe" in out.lower()
