from shannon_core.renderers.pre_recon import render_pre_recon
from shannon_core.renderers import render_deliverable
from shannon_core.models.agents import AgentName


def test_empty_data_renders_preamble_and_all_placeholders():
    md = render_pre_recon({})
    assert md.startswith("# Penetration Test Scope & Boundaries")
    assert "## 1. Executive Summary" in md
    assert "## 10. SSRF Sinks" in md
    for tool in [
        "set_executive_summary", "set_application_intelligence", "set_auth_deep_dive",
        "set_codebase_indexing", "set_critical_file_paths", "set_xss_sinks", "set_ssrf_sinks",
    ]:
        assert f"`{tool}` was not called" in md


def test_executive_summary_section_renders_text():
    md = render_pre_recon({"executive_summary": {"text": "The app is risky."}})
    assert "## 1. Executive Summary" in md
    assert "The app is risky." in md


def test_application_intelligence_feeds_sections_2_4_5_6():
    md = render_pre_recon({
        "application_intelligence": {
            "architecture": {"framework_and_language": "Express + Node", "architectural_pattern": "MVC",
                             "critical_security_components": "passport"},
            "data_security": {"database_security": "parametrized", "data_flow_security": "tls",
                              "multi_tenant_isolation": "n/a"},
            "attack_surface": {"external_entry_points": "/login", "internal_service_communication": "none",
                               "input_validation_patterns": "express-validator", "background_processing": "bull"},
            "infrastructure": {"secrets_management": "env", "configuration_security": "helmet",
                               "external_dependencies": "lodash", "monitoring_and_logging": "winston"},
        }
    })
    assert "## 2. Architecture & Technology Stack" in md and "Express + Node" in md
    assert "## 4. Data Security & Storage" in md and "parametrized" in md
    assert "## 5. Attack Surface Analysis" in md and "/login" in md
    assert "## 6. Infrastructure & Operational Security" in md and "winston" in md


def test_auth_deep_dive_section_renders_with_null_sso():
    md = render_pre_recon({"auth_deep_dive": {
        "authentication_mechanisms": "session", "session_management": "cookie HttpOnly",
        "authz_model": "rbac", "multi_tenancy": "single", "sso_oauth_oidc": None,
    }})
    assert "## 3. Authentication & Authorization Deep Dive" in md
    assert "session" in md


def test_xss_applicable_false_renders_na():
    md = render_pre_recon({"xss_sinks": {"applicable": False}})
    assert "N/A — the application has no web frontend; XSS sink analysis does not apply." in md


def test_xss_with_empty_sink_arrays_renders_scanned_placeholder():
    md = render_pre_recon({"xss_sinks": {
        "applicable": True, "html_body": [], "html_attribute": [],
        "javascript": [], "css": [], "url": [],
    }})
    assert "scanned, no sinks of this kind found" in md


def test_xss_with_sinks_renders_location_and_sink_function():
    md = render_pre_recon({"xss_sinks": {
        "applicable": True,
        "html_body": [{"location": "render.js:34", "sink_function": "innerHTML", "notes": "user input"}],
        "html_attribute": [], "javascript": [], "css": [], "url": [],
    }})
    assert "render.js:34" in md and "innerHTML" in md


def test_ssrf_applicable_false_renders_na():
    md = render_pre_recon({"ssrf_sinks": {"applicable": False}})
    assert "N/A — the application makes no outbound requests; SSRF sink analysis does not apply." in md


def test_critical_file_paths_empty_array_renders_none_identified():
    md = render_pre_recon({"critical_file_paths": {"configuration": [], "api_and_routing": ["routes.js"]}})
    assert "none identified" in md
    assert "routes.js" in md


def test_full_payload_byte_stability():
    md = render_pre_recon({
        "executive_summary": {"text": "OVERVIEW."},
        "application_intelligence": {
            "architecture": {"framework_and_language": "F", "architectural_pattern": "P", "critical_security_components": "C"},
            "data_security": {"database_security": "d1", "data_flow_security": "d2", "multi_tenant_isolation": "d3"},
            "attack_surface": {"external_entry_points": "a1", "internal_service_communication": "a2",
                               "input_validation_patterns": "a3", "background_processing": "a4"},
            "infrastructure": {"secrets_management": "i1", "configuration_security": "i2",
                               "external_dependencies": "i3", "monitoring_and_logging": "i4"},
        },
        "auth_deep_dive": {"authentication_mechanisms": "m", "session_management": "s", "authz_model": "z",
                           "multi_tenancy": "t", "sso_oauth_oidc": None},
        "codebase_indexing": {"text": "INDEX."},
        "critical_file_paths": {"configuration": ["c.js"]},
        "xss_sinks": {"applicable": False},
        "ssrf_sinks": {"applicable": False},
    })
    assert md.count("# Penetration Test Scope & Boundaries") == 1
    assert md.endswith("\n")
    assert "OVERVIEW." in md and "INDEX." in md


def test_render_deliverable_dispatches_pre_recon():
    md = render_deliverable(AgentName.PRE_RECON, {"executive_summary": {"text": "x"}})
    assert md is not None and "## 1. Executive Summary" in md


def test_render_deliverable_returns_none_for_non_rendered_agent():
    # RECON 现已接 host-render（Plan 2 Task C）；用 REPORT 验证无 renderer 通道的
    # agent 仍返 None（对齐 collector 侧 test_pre_recon.py:106 的 RECON→REPORT 修法）。
    assert render_deliverable(AgentName.REPORT, {}) is None
