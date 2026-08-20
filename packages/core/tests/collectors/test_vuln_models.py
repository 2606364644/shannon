"""vuln 5 class 共用 collector 的 section schema 测试。

对照 TS apps/worker/src/collectors/vuln-collector.ts。
Plan 3 Task 1 起为 3 shared section（findings_summary/safe_vectors/blind_spots）
+ per-class strategic_intelligence section；Phase 2（spec 2026-08-19 §3.3）加
submit_finding 单条上交 section（per-class schema），共 5 工具/5 section。
注意：SectionSchema 携带 JSON Schema dict（非 pydantic model_cls）。
"""
import pytest

from supernova_core.collectors.base import CollectorBase, SectionSchema
from supernova_core.collectors.vuln import (
    AUTHZ_STRATEGIC_INTEL,
    AUTH_STRATEGIC_INTEL,
    FINDINGS_SUMMARY,
    INJECTION_STRATEGIC_INTEL,
    SSRF_STRATEGIC_INTEL,
    VULN_CLASSES,
    XSS_STRATEGIC_INTEL,
    make_vuln_collector,
    make_vuln_sections,
)

EXPECTED_TOOL_NAMES = [
    "submit_finding",
    "set_findings_summary",
    "set_strategic_intelligence",
    "set_safe_vectors",
    "set_blind_spots",
]
EXPECTED_SECTION_KEYS = [
    "submitted_findings",
    "findings_summary",
    "strategic_intelligence",
    "safe_vectors",
    "blind_spots",
]


def test_five_vuln_classes_exactly():
    assert set(VULN_CLASSES) == {"injection", "xss", "auth", "ssrf", "authz"}


def test_each_class_has_tools_in_ts_order():
    """每个 vuln class 的 5 工具（Phase 2 起含 submit_finding）按 TS 顺序注册。"""
    for vc in VULN_CLASSES:
        sections = make_vuln_sections(vc)
        assert [s.tool_name for s in sections] == EXPECTED_TOOL_NAMES, vc
        assert [s.section_key for s in sections] == EXPECTED_SECTION_KEYS, vc


def test_each_section_is_section_schema_with_json_schema_object():
    for vc in VULN_CLASSES:
        for s in make_vuln_sections(vc):
            assert isinstance(s, SectionSchema)
            assert s.json_schema["type"] == "object"
            assert "properties" in s.json_schema
            assert "required" in s.json_schema


def test_strategic_intel_schema_differs_per_class():
    """injection 的 strategic_intel section 携带 INJECTION_STRATEGIC_INTEL，
    xss 携带 XSS_STRATEGIC_INTEL；5 个 per-class schema 各不相同。"""
    inj = next(
        s for s in make_vuln_sections("injection")
        if s.tool_name == "set_strategic_intelligence"
    )
    xss = next(
        s for s in make_vuln_sections("xss")
        if s.tool_name == "set_strategic_intelligence"
    )
    assert inj.json_schema is INJECTION_STRATEGIC_INTEL
    assert xss.json_schema is XSS_STRATEGIC_INTEL
    assert inj.json_schema is not xss.json_schema

    all_schemas = {
        vc: next(
            s for s in make_vuln_sections(vc)
            if s.tool_name == "set_strategic_intelligence"
        ).json_schema
        for vc in VULN_CLASSES
    }
    # 5 个 schema 两两不同（指针 + properties 双重校验）
    seen: list[dict] = []
    for vc, schema in all_schemas.items():
        assert schema not in seen, f"{vc} strategic intel duplicates another class"
        seen.append(schema)


def test_shared_sections_identical_across_classes():
    """3 shared set_* section 的 json_schema pointer 在所有 class 上一致（单一声明）。

    submit_finding 与 set_strategic_intelligence 是 per-class schema，不在此列。"""
    by_tool: dict[str, dict] = {}
    for vc in VULN_CLASSES:
        for s in make_vuln_sections(vc):
            if s.tool_name in ("submit_finding", "set_strategic_intelligence"):
                continue
            by_tool.setdefault(s.tool_name, s.json_schema)
            assert by_tool[s.tool_name] is s.json_schema, (
                f"{s.tool_name} must be the same object across vuln classes"
            )


def test_findings_summary_schema_structure():
    sections = make_vuln_sections("injection")
    s = next(x for x in sections if x.tool_name == "set_findings_summary")
    props = s.json_schema["properties"]
    assert set(props) == {"key_outcome", "patterns", "finding_roster"}
    assert props["key_outcome"]["type"] == "string"
    assert props["patterns"]["type"] == "array"
    pattern_item = props["patterns"]["items"]
    assert set(pattern_item["properties"]) == {
        "name", "description", "implication", "representative_finding_ids",
    }
    assert pattern_item["properties"]["representative_finding_ids"]["items"]["minLength"] == 1
    assert props["finding_roster"]["type"] == "array"
    assert s.json_schema["required"] == ["key_outcome", "patterns", "finding_roster"]


def test_safe_vectors_schema_structure():
    sections = make_vuln_sections("injection")
    s = next(x for x in sections if x.tool_name == "set_safe_vectors")
    props = s.json_schema["properties"]
    assert set(props) == {"vectors"}
    vec_item = props["vectors"]["items"]
    assert set(vec_item["properties"]) == {
        "subject", "location", "defense_mechanism", "render_context",
    }
    # render_context XSS-only，optional：不在 required 里
    assert "render_context" not in vec_item.get("required", [])
    assert s.json_schema["required"] == ["vectors"]


def test_blind_spots_schema_structure():
    sections = make_vuln_sections("injection")
    s = next(x for x in sections if x.tool_name == "set_blind_spots")
    props = s.json_schema["properties"]
    assert set(props) == {"items"}
    item = props["items"]["items"]
    assert set(item["properties"]) == {"heading", "description"}
    assert s.json_schema["required"] == ["items"]


def test_injection_strategic_intel_fields_match_ts():
    """对照 TS InjectionStrategicIntelSchema 的 3 字段。"""
    assert set(INJECTION_STRATEGIC_INTEL["properties"]) == {
        "defensive_evasion_waf", "error_based_potential", "confirmed_database_technology",
    }
    assert set(INJECTION_STRATEGIC_INTEL["required"]) == {
        "defensive_evasion_waf", "error_based_potential", "confirmed_database_technology",
    }


def test_xss_strategic_intel_fields_match_ts():
    assert set(XSS_STRATEGIC_INTEL["properties"]) == {"csp_analysis", "cookie_security"}
    assert set(XSS_STRATEGIC_INTEL["required"]) == {"csp_analysis", "cookie_security"}


def test_auth_strategic_intel_fields_match_ts():
    assert set(AUTH_STRATEGIC_INTEL["properties"]) == {
        "authentication_method", "session_token_details", "password_policy",
    }
    assert set(AUTH_STRATEGIC_INTEL["required"]) == {
        "authentication_method", "session_token_details", "password_policy",
    }


def test_ssrf_strategic_intel_fields_match_ts():
    assert set(SSRF_STRATEGIC_INTEL["properties"]) == {
        "http_client_library", "request_architecture", "internal_services",
    }
    assert set(SSRF_STRATEGIC_INTEL["required"]) == {
        "http_client_library", "request_architecture", "internal_services",
    }


def test_authz_strategic_intel_fields_match_ts():
    assert set(AUTHZ_STRATEGIC_INTEL["properties"]) == {
        "session_management_architecture", "role_permission_model",
        "resource_access_patterns", "workflow_implementation",
    }
    assert set(AUTHZ_STRATEGIC_INTEL["required"]) == {
        "session_management_architecture", "role_permission_model",
        "resource_access_patterns", "workflow_implementation",
    }


def test_make_vuln_collector_returns_collectorbase_with_sections():
    """make_vuln_collector 返回 CollectorBase，注册 5 工具/5 section，初始全 skipped。"""
    for vc in VULN_CLASSES:
        c = make_vuln_collector(vc)
        assert isinstance(c, CollectorBase)
        assert c.tool_names() == EXPECTED_TOOL_NAMES
        assert [s.section_key for s in c.section_schemas] == EXPECTED_SECTION_KEYS
        assert c.get_call_status() == {t: "skipped" for t in EXPECTED_TOOL_NAMES}


def test_unknown_vuln_class_raises_value_error():
    with pytest.raises(ValueError):
        make_vuln_sections("nonsense")
    with pytest.raises(ValueError):
        make_vuln_collector("nonsense")


# ── Phase 2（spec 2026-08-19 §3.3）：submit_finding 单条上交 + finding_roster ──

def test_submit_finding_is_append_mode_single_object_schema():
    """submit_finding：mode=append（可多次调累积）、schema 是单个 finding object（非 array）。"""
    from supernova_core.collectors.base import CollectorBase

    for vc in VULN_CLASSES:
        sections = make_vuln_sections(vc)
        sub = sections[0]
        assert sub.tool_name == "submit_finding" and sub.section_key == "submitted_findings"
        assert sub.mode == "append", vc
        assert sub.json_schema["type"] == "object"  # 单条，非 array
        # 基线 required（title 进 required——roster 对账依赖）
        for f in ("ID", "vulnerability_type", "externally_exploitable", "confidence", "title"):
            assert f in sub.json_schema["required"], (vc, f)
        # collector 实际可累积多条（append 语义）
        c = CollectorBase(section_schemas=make_vuln_sections(vc))
        c.append_section("submit_finding", {"ID": "AUTH-VULN-01", "title": "a", "notes": "n"})
        c.append_section("submit_finding", {"ID": "AUTH-VULN-02", "title": "b"})
        assert [f["ID"] for f in c.get_all()["submitted_findings"]] == ["AUTH-VULN-01", "AUTH-VULN-02"]


def test_finding_schema_class_specific_fields():
    """per-class finding schema：class 特有字段各不相同（宽松 optional，无 enum）。"""
    def _props(vc):
        sub = make_vuln_sections(vc)[0]
        return set(sub.json_schema["properties"]) - {
            "ID", "vulnerability_type", "externally_exploitable", "confidence", "title", "notes"}

    assert _props("auth") == {
        "source_endpoint", "vulnerable_code_location", "missing_defense",
        "exploitation_hypothesis", "suggested_exploit_technique"}
    assert _props("ssrf") == _props("auth") | {"vulnerable_parameter"}
    assert _props("authz") == {
        "endpoint", "vulnerable_code_location", "role_context", "guard_evidence",
        "side_effect", "reason", "minimal_witness"}
    # injection 与 xss 共用 XSS 风格字段（对齐 queue_schemas.py 注释：两轨同 schema）
    assert _props("injection") == _props("xss") == {
        "source", "source_detail", "path", "sink_function", "render_context",
        "encoding_observed", "verdict", "mismatch_reason", "witness_payload"}


def test_findings_summary_roster_required():
    """set_findings_summary 的 finding_roster：required、list[{id,title}] 结构。"""
    props = FINDINGS_SUMMARY["properties"]
    assert "finding_roster" in FINDINGS_SUMMARY["required"]
    item = props["finding_roster"]["items"]
    assert set(item["required"]) == {"id", "title"}
    # 空数组合法（= 声明无漏洞）
    assert props["finding_roster"]["type"] == "array"
    assert props["finding_roster"].get("minItems") is None
