from supernova_core.models.queue_schemas import (
    BaseVulnerability,
    InjectionVulnerability,
    VulnerabilityQueue,
)


def _base(**kw):
    data = {
        "ID": "V1",
        "vulnerability_type": "injection",
        "externally_exploitable": True,
        "confidence": "high",
    }
    data.update(kw)
    return BaseVulnerability(
        **data,
    )


def test_base_vulnerability_has_source_track_defaults_none():
    v = _base()
    assert v.source_track is None
    assert v.evidence_chain is None
    assert v.merge_source is None


def test_base_vulnerability_accepts_new_fields():
    v = _base(
        source_track="gitnexus",
        evidence_chain="q -> db.exe(L42)",
        merge_source="both",
    )
    assert v.source_track == "gitnexus"
    assert v.evidence_chain == "q -> db.exe(L42)"
    assert v.merge_source == "both"


def test_subclass_inherits_new_fields():
    v = InjectionVulnerability(
        ID="I1",
        vulnerability_type="injection",
        externally_exploitable=True,
        confidence="high",
        source_track="llm",
        merge_source="llm-only",
    )
    assert v.source_track == "llm"
    assert v.merge_source == "llm-only"


def test_legacy_queue_without_new_fields_parses():
    """Legacy queue files without dual-track fields still parse."""
    content = (
        '{"vulnerabilities":[{"ID":"L1","vulnerability_type":"injection",'
        '"externally_exploitable":true,"confidence":"high"}]}'
    )
    result = VulnerabilityQueue.parse_lenient(content)
    assert result.warnings == [] or all("dropped" not in w for w in result.warnings)
    assert len(result.queue.vulnerabilities) == 1
    v = result.queue.vulnerabilities[0]
    assert v.merge_source is None


def test_needs_review_confidence_value_allowed():
    v = _base(confidence="needs_review")
    assert v.confidence == "needs_review"


# 报告可读性改造（spec 2026-08-25 §4）：BaseVulnerability 报告字段 append-only 扩展。
_REPORT_FIELDS = (
    "severity",
    "cvss",
    "cwe_id",
    "owasp_category",
    "endpoint",
    "affected_parameters",
    "affected_entries",
    "verification",
    "code_snippet",
)


def test_report_readability_fields_default_none():
    v = _base()
    for name in _REPORT_FIELDS:
        assert getattr(v, name) is None, name


def test_report_readability_fields_accept_values():
    v = _base(
        severity="high",
        cvss="AV:N/AC:L/PR:L/UI:N 8.8",
        cwe_id="CWE-95",
        owasp_category="A03:2021-Injection",
        endpoint="POST /contributions",
        affected_parameters=["name"],
        affected_entries=[
            {
                "parameter": "name",
                "sink_location": "eval:32:23",
                "chain_id": "C1",
                "track": "llm",
                "direct": True,
            }
        ],
        verification="static_analysis",
        code_snippet="eval(userInput)",
    )
    assert v.severity == "high"
    assert v.cvss == "AV:N/AC:L/PR:L/UI:N 8.8"
    assert v.cwe_id == "CWE-95"
    assert v.owasp_category == "A03:2021-Injection"
    assert v.endpoint == "POST /contributions"
    assert v.affected_parameters == ["name"]
    assert v.affected_entries[0]["parameter"] == "name"
    assert v.verification == "static_analysis"
    assert v.code_snippet == "eval(userInput)"


def test_report_readability_fields_inherited_by_subclass():
    v = InjectionVulnerability(
        ID="I2",
        vulnerability_type="injection",
        externally_exploitable=True,
        confidence="high",
        severity="critical",
        endpoint="GET /search",
    )
    assert v.severity == "critical"
    assert v.endpoint == "GET /search"
