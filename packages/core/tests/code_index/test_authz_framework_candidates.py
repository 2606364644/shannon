# packages/core/tests/code_index/test_authz_framework_candidates.py
import json

from supernova_core.code_index.authz_gitnexus_track import (
    FrameworkIDORCandidate,
    find_framework_idor_candidates,
)


def _fa(endpoints):
    return {"detected_framework": {"name": "finale-rest"},
            "inferred_endpoints": endpoints,
            "recommendations": []}


def test_framework_auto_generated_endpoints_are_candidates(tmp_path):
    fa = _fa([
        {"method": "DELETE", "path": "/api/Feedbacks/:id",
         "source": "framework-auto-generated", "model": "Feedback",
         "middleware": ("isAuthenticated",),
         "vulnerability_indicators": ("No ownership check on finale resource operations",)},
        {"method": "GET", "path": "/api/Feedbacks",
         "source": "framework-auto-generated", "model": "Feedback",
         "middleware": ("isAuthenticated",), "vulnerability_indicators": ()},
    ])
    fa_path = tmp_path / "framework_analysis.json"
    fa_path.write_text(json.dumps(fa))

    cands = find_framework_idor_candidates(fa_path)
    assert len(cands) == 2
    assert all(c.framework == "finale-rest" for c in cands)
    methods = {c.method for c in cands}
    assert methods == {"DELETE", "GET"}
    assert cands[0].model == "Feedback"


def test_manual_endpoints_excluded(tmp_path):
    fa = _fa([
        {"method": "DELETE", "path": "/api/x/:id", "source": "manual",
         "model": None, "middleware": (), "vulnerability_indicators": ()},
    ])
    fa_path = tmp_path / "framework_analysis.json"
    fa_path.write_text(json.dumps(fa))
    assert find_framework_idor_candidates(fa_path) == []


def test_missing_framework_file_yields_empty(tmp_path):
    """Plan 2 not landed → no framework_analysis.json → empty (graceful)."""
    cands = find_framework_idor_candidates(tmp_path / "framework_analysis.json")
    assert cands == []


def test_invalid_framework_json_yields_empty(tmp_path):
    fa_path = tmp_path / "framework_analysis.json"
    fa_path.write_text("not json")
    assert find_framework_idor_candidates(fa_path) == []
