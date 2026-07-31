"""vuln renderer TDD — 5 vuln class 共用 render_vuln。"""
import pytest

from supernova_core.renderers._helpers import placeholder, render_table
from supernova_core.renderers.vuln import render_vuln


@pytest.fixture(autouse=True)
def _en_lang_default(monkeypatch):
    """断言基于英文渲染（i18n 前行为）；默认 en。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")


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


def test_strategic_intel_schema_keys_match_renderer_subheaders():
    """跨层不变量:collector strategic_intel schema 字段 == renderer §3 subheader 字段。

    防单边改名致 renderer 静默 drop §3 子段(配合 _strategic_intel 的 skip-missing
    行为,drift 会让字段悄悄消失——brief 担心的 silent drop 风险)。用公共 API,
    不依赖私有名。(final-review M1 follow-up 落地)
    """
    from supernova_core.collectors.vuln import VULN_CLASSES, make_vuln_sections
    from supernova_core.renderers.vuln import STRATEGIC_INTEL_SUBHEADERS

    for vc in VULN_CLASSES:
        sections = make_vuln_sections(vc)
        intel = next(s for s in sections if s.tool_name == "set_strategic_intelligence")
        schema_fields = set(intel.json_schema["properties"].keys())
        renderer_fields = {f for f, _ in STRATEGIC_INTEL_SUBHEADERS[vc]}
        assert schema_fields == renderer_fields, (
            f"{vc}: collector schema {schema_fields} ↔ renderer subheaders "
            f"{renderer_fields} drift"
        )


# ── schema 违规止血(2026-07-20) ───────────────────────────────────────────
def test_findings_summary_as_str_does_not_crash():
    md = render_vuln("injection", {"findings_summary": "prose"})
    assert "## 1. Executive Summary" in md


def test_patterns_str_elements_skipped():
    md = render_vuln("injection", {"findings_summary": {
        "patterns": ["str instead of dict"]}})
    assert "## 2. Dominant Vulnerability Patterns" in md
    assert "No dominant patterns identified" in md


def test_safe_vectors_str_elements_skipped():
    md = render_vuln("xss", {"safe_vectors": {"vectors": ["str elem"]}})
    assert "No vectors confirmed secure" in md
    assert "str elem" not in md


def test_blind_spots_as_str_does_not_crash():
    md = render_vuln("auth", {"blind_spots": "prose"})
    assert "## 5. Analysis Constraints and Blind Spots" in md


def test_render_vuln_zh_titles_and_sections(monkeypatch):
    """zh 模式：标题 + section 标题为中文。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    md = render_vuln("injection", {})
    assert "# 注入分析报告" in md
    assert "## 1. 执行摘要" in md
    assert "## 2. 主要漏洞模式" in md
    assert "## 5. 分析约束与盲区" in md
    assert "Executive Summary" not in md
