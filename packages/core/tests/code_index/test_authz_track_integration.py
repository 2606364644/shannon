# packages/core/tests/code_index/test_authz_track_integration.py
"""End-to-end: code_index.json + framework_analysis.json → build_authz_gitnexus_track.

Covers dominance candidates (handler→sink no ownership), framework candidates
(auto-generated CRUD), evidence rendering, and graceful degradation.
"""
import json

from shannon_core.code_index.authz_gitnexus_track import build_authz_gitnexus_track


def _block(bid, source):
    fp, fn, ln = bid.rsplit(":", 2)
    return {"id": bid, "file_path": fp, "function_name": fn, "start_line": int(ln),
            "end_line": int(ln) + 5, "source_code": source, "parameters": [],
            "decorators": [], "language": "typescript"}


def _ep(handler, route, method="DELETE"):
    return {"func_block_id": handler, "entry_type": "http_route", "route": route,
            "http_method": method, "confidence": 0.9, "evidence": "",
            "needs_llm_review": False, "authentication": "required", "source": "code_index"}


def _write(tmp_path, eps, blocks, chains, fw_endpoints=None):
    (tmp_path / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "typescript", "total_blocks": len(blocks),
        "total_entry_points": len(eps), "total_chains": len(chains),
        "blocks": blocks, "edges": [], "entry_points": eps, "chains": chains,
    }))
    if fw_endpoints is not None:
        (tmp_path / "framework_analysis.json").write_text(json.dumps({
            "detected_framework": {"name": "finale-rest"},
            "inferred_endpoints": fw_endpoints, "recommendations": [],
        }))


def test_e2e_dominance_candidate_plus_framework_candidate(tmp_path):
    handler = _block("u.js:update:10", "async function update(req){ await repo.update(req.params.id); }")
    guarded = _block("g.js:safe:1",
                     "async function safe(req){ const r = await db.user.findFirst({where:{userId:req.user.id}}); await r.save(); }")
    sink = _block("repo.js:update:1", "function update(){ db.user.update(); }")
    safe_sink = _block("repo.js:safeupdate:1", "function safeupdate(){ r.save(); }")
    _write(
        tmp_path,
        [_ep("u.js:update:10", "/api/Feedbacks/:id", "DELETE"),
         _ep("g.js:safe:1", "/api/owned/:id", "PUT")],
        [handler, guarded, sink, safe_sink],
        [
            {"entry_point_id": "u.js:update:10", "path": ["u.js:update:10", "repo.js:update:1"],
             "depth": 1, "has_unresolved": False},
            {"entry_point_id": "g.js:safe:1", "path": ["g.js:safe:1", "repo.js:safeupdate:1"],
             "depth": 1, "has_unresolved": False},  # guarded → not a candidate
        ],
        fw_endpoints=[
            {"method": "DELETE", "path": "/api/Feedbacks", "source": "framework-auto-generated",
             "model": "Feedback", "middleware": ("isAuthenticated",),
             "vulnerability_indicators": ("No ownership check",)},
        ],
    )

    md, dom, fw, http_route_count, entry_point_total = build_authz_gitnexus_track(str(tmp_path))

    # dominance: only the unguarded handler → 1 candidate (guarded handler skipped)
    assert len(dom) == 1
    assert dom[0].sink_id == "repo.js:update:1"
    # framework: 1 auto-generated
    assert len(fw) == 1
    assert fw[0].method == "DELETE"
    # markdown surfaces both
    assert "DELETE /api/Feedbacks/:id" in md
    assert "repo.js:update:1" in md
    assert "finale-rest" in md
    # verdict directive
    assert "verdict" in md.lower() or "判定" in md


def test_e2e_graceful_degradation_no_index(tmp_path):
    md, dom, fw, http_route_count, entry_point_total = build_authz_gitnexus_track(str(tmp_path))
    assert "无" in md or "no" in md.lower()
    assert dom == [] and fw == []


def test_e2e_guarded_handler_not_a_candidate(tmp_path):
    """Handler with ORM ownership predicate → no dominance candidate even if it reaches a sink."""
    handler = _block("u.js:update:10",
                     "async function update(req){ const r = await db.user.findFirst({where:{userId:req.user.id}}); await repo.update(r); }")
    sink = _block("repo.js:update:1", "function update(){ db.user.update(); }")
    _write(
        tmp_path,
        [_ep("u.js:update:10", "/api/u/:id", "PUT")],
        [handler, sink],
        [{"entry_point_id": "u.js:update:10", "path": ["u.js:update:10", "repo.js:update:1"],
          "depth": 1, "has_unresolved": False}],
    )
    md, dom, fw, http_route_count, entry_point_total = build_authz_gitnexus_track(str(tmp_path))
    assert dom == []  # ownership guard present
