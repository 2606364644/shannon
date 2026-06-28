# packages/core/tests/code_index/test_authz_build_track.py
import json

from shannon_core.code_index.authz_gitnexus_track import build_authz_gitnexus_track


def _block(bid, source):
    fp, fn, ln = bid.rsplit(":", 2)
    return {"id": bid, "file_path": fp, "function_name": fn, "start_line": int(ln),
            "end_line": int(ln) + 3, "source_code": source, "parameters": [],
            "decorators": [], "language": "typescript"}


def _ep(handler, route, method="DELETE"):
    return {"func_block_id": handler, "entry_type": "http_route", "route": route,
            "http_method": method, "confidence": 0.9, "evidence": "",
            "needs_llm_review": False, "authentication": "required", "source": "code_index"}


def _write_index(tmp_path, eps, blocks):
    (tmp_path / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "typescript",
        "total_blocks": len(blocks), "total_entry_points": len(eps),
        "total_chains": 1, "blocks": blocks, "edges": [],
        "entry_points": eps,
        "chains": [{"entry_point_id": eps[0]["func_block_id"],
                    "path": [eps[0]["func_block_id"], blocks[-1]["id"]],
                    "depth": 1, "has_unresolved": False}] if eps else [],
    }))


def _write_framework(tmp_path, endpoints):
    (tmp_path / "framework_analysis.json").write_text(json.dumps({
        "detected_framework": {"name": "finale-rest"},
        "inferred_endpoints": endpoints, "recommendations": [],
    }))


def test_build_dominance_and_framework_candidates(tmp_path):
    handler = _block("u.js:update:10", "async function update(req){ await repo.update(req.params.id); }")
    sink = _block("repo.js:update:1", "function update(){ db.user.update(); }")
    _write_index(tmp_path, [_ep("u.js:update:10", "/api/u/:id", "PUT")], [handler, sink])
    _write_framework(tmp_path, [
        {"method": "DELETE", "path": "/api/Feedbacks/:id", "source": "framework-auto-generated",
         "model": "Feedback", "middleware": ("isAuthenticated",),
         "vulnerability_indicators": ("No ownership check on finale resource operations",)},
    ])

    md, dom_cands, fw_cands, http_route_count, entry_point_total = build_authz_gitnexus_track(str(tmp_path))
    assert len(dom_cands) == 1
    assert dom_cands[0].sink_id == "repo.js:update:1"
    assert len(fw_cands) == 1
    assert fw_cands[0].method == "DELETE"
    # markdown surfaces both
    assert "PUT /api/u/:id" in md
    assert "DELETE /api/Feedbacks/:id" in md
    assert "verdict" in md.lower() or "判定" in md


def test_build_missing_code_index_returns_empty(tmp_path):
    md, dom, fw, http_route_count, entry_point_total = build_authz_gitnexus_track(str(tmp_path))
    assert "无" in md or "no" in md.lower()
    assert dom == [] and fw == []


def test_build_framework_only_when_code_index_empty(tmp_path):
    (tmp_path / "code_index.json").write_text(json.dumps({
        "repository": "r", "language": "typescript", "total_blocks": 0,
        "total_entry_points": 0, "total_chains": 0, "blocks": [], "edges": [],
        "entry_points": [], "chains": [],
    }))
    _write_framework(tmp_path, [
        {"method": "DELETE", "path": "/api/F/:id", "source": "framework-auto-generated",
         "model": "F", "middleware": (), "vulnerability_indicators": ()},
    ])
    md, dom, fw, http_route_count, entry_point_total = build_authz_gitnexus_track(str(tmp_path))
    assert dom == []  # no chains
    assert len(fw) == 1
    assert "DELETE /api/F/:id" in md


def test_build_invalid_code_index_returns_empty(tmp_path):
    (tmp_path / "code_index.json").write_text("not json")
    md, dom, fw, http_route_count, entry_point_total = build_authz_gitnexus_track(str(tmp_path))
    assert isinstance(md, str)
    assert dom == []


def test_build_returns_diagnostic_fields(tmp_path):
    handler = _block("u.js:update:10", "async function update(req){ await repo.update(req.params.id); }")
    sink = _block("repo.js:update:1", "function update(){ db.user.update(); }")
    _write_index(tmp_path, [_ep("u.js:update:10", "/api/u/:id", "PUT")], [handler, sink])

    result = build_authz_gitnexus_track(str(tmp_path))

    assert result.entry_point_total == 1
    assert result.http_route_count == 1
    assert result.markdown and "PUT /api/u/:id" in result.markdown
    assert len(result.dominance_candidates) == 1
    assert result.dominance_candidates[0].sink_id == "repo.js:update:1"


def test_build_diagnostic_fields_zero_when_empty(tmp_path):
    # code_index 缺失 → 空 CodeIndex → 诊断字段皆为 0
    result = build_authz_gitnexus_track(str(tmp_path))
    assert result.entry_point_total == 0
    assert result.http_route_count == 0
    assert result.dominance_candidates == []
    assert result.framework_candidates == []
