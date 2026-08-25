# packages/core/tests/code_index/test_gn_collapse.py
from supernova_core.code_index.gn_collapse import (
    collapse_gn_entries, extract_endpoint, extract_param, parse_sink_call_site_id,
)
from supernova_core.models.queue_schemas import InjectionVulnerability

def _gn(id_, param, sink, path="POST /contributions → chain", severity=None):
    return InjectionVulnerability(
        ID=id_, vulnerability_type="injection", externally_exploitable=True,
        confidence="low", source=f"{param} (app/routes/contributions.js:ContributionsHandler:7)",
        path=path, sink_call=sink, verdict="vulnerable", source_track="gitnexus",
        severity=severity)

SINK32 = "app/routes/contributions.js:ContributionsHandler:eval:32:23"
SINK33 = "app/routes/contributions.js:ContributionsHandler:eval:33:25"

def test_parse_sink_call_site_id():
    assert parse_sink_call_site_id(SINK32) == ("eval", "app/routes/contributions.js:32")
    assert parse_sink_call_site_id("short") == (None, None)

def test_extract_endpoint_and_param():
    assert extract_endpoint("POST /contributions → preTax -> x") == "POST /contributions"
    assert extract_endpoint("a → GET /login → b") == "GET /login"
    assert extract_endpoint("no route here") is None
    assert extract_param("preTax (app/routes/contributions.js:7)") == "preTax"

def test_collapse_same_unit_nine_to_three():
    """preTax/afterTax/roth × eval:32/33/34（同接口同 sink 函数）→ 1 主记录 9 入口行。"""
    gn = [_gn(f"INJ-GN-{i:02d}", p, s)
          for i, (p, s) in enumerate(
              [(p, f"app/routes/contributions.js:ContributionsHandler:eval:{ln}:{ln}")
               for p in ("preTax", "afterTax", "roth") for ln in (32, 33, 34)], start=1)]
    out = collapse_gn_entries(gn)
    assert len(out) == 1
    assert out[0].ID == "INJ-GN-01"
    assert out[0].endpoint == "POST /contributions"
    assert set(out[0].affected_parameters) == {"preTax", "afterTax", "roth"}
    assert len(out[0].affected_entries) == 9
    assert out[0].affected_entries[0] == {
        "parameter": "preTax", "sink_location": "app/routes/contributions.js:32",
        "chain_id": "INJ-GN-01", "track": "gitnexus"}

def test_collapse_keeps_different_endpoints_separate():
    a = _gn("XSS-GN-01", "memo", "app/routes/memos.js:MemosHandler:render:27:19",
            path="GET /memos → chain")
    b = _gn("XSS-GN-02", "url", "app/routes/research.js:ResearchHandler:render:31:15",
            path="GET /research → chain")
    out = collapse_gn_entries([a, b])
    assert len(out) == 2  # 不同接口绝不合并（spec §3.1）

def test_collapse_severity_takes_max():
    gn = [_gn("INJ-GN-01", "preTax", SINK32, severity="medium"),
          _gn("INJ-GN-02", "preTax", SINK33, severity=None)]  # 兜底 critical(eval)
    out = collapse_gn_entries(gn)
    assert out[0].severity == "critical"
