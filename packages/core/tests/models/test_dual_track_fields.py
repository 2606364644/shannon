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
