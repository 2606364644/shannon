"""vuln renderer TDD — 5 vuln class 共用 render_vuln。"""
from shannon_core.renderers._helpers import placeholder, render_table
from shannon_core.renderers.vuln import render_vuln


def test_all_missing_renders_placeholders_per_class():
    for vc in ["injection", "xss", "auth", "ssrf", "authz"]:
        md = render_vuln(vc, {})
        assert placeholder("Section 1", "set_findings_summary") in md, vc
        assert placeholder("Section 3", "set_strategic_intelligence") in md, vc


def test_findings_summary_renders_outcome_and_patterns():
    md = render_vuln("auth", {"findings_summary": {
        "key_outcome": "weak pwd policy",
        "patterns": [{"name": "Weak Session", "description": "d", "implication": "i",
                      "representative_finding_ids": ["AUTH-VULN-1"]}]}})
    assert "## 1. Executive Summary" in md and "weak pwd policy" in md
    assert "## 2. Dominant Vulnerability Patterns" in md and "Weak Session" in md


def test_xss_has_render_context_column_and_non_xss_does_not():
    xss_md = render_vuln("xss", {"safe_vectors": {"vectors": [
        {"subject": "q", "location": "s.js:1", "defense_mechanism": "escape",
         "render_context": "HTML_BODY"}]}})
    assert "Render Context" in xss_md and "HTML_BODY" in xss_md

    inj_md = render_vuln("injection", {"safe_vectors": {"vectors": [
        {"subject": "q", "location": "s.js:1", "defense_mechanism": "prepared"}]}})
    assert "Render Context" not in inj_md


def test_strategic_intel_uses_per_class_subheaders():
    md = render_vuln("ssrf", {"strategic_intelligence": {
        "http_client_library": "axios", "request_architecture": "server",
        "internal_services": "none"}})
    assert "## 3. Strategic Intelligence" in md and "axios" in md


def test_helpers_render_table_escapes_pipe_and_drops_empty_rows():
    assert render_table(["a", "b"], []) == ""
    md = render_table(["h1", "h2"], [["x", "p|q"], ["y", "z"]])
    assert md.startswith("| h1 | h2 |")
    assert "---" in md
    assert "p\\|q" in md  # pipe escaped
