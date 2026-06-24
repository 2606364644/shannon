from shannon_core.code_index.dual_track_merger import merge_dual_track_queues
from shannon_core.models.queue_schemas import AuthzVulnerability, InjectionVulnerability


def _inj(ID, verdict, source="q", sink_call="db.exec", **kw):
    return InjectionVulnerability(
        ID=ID,
        vulnerability_type="injection",
        externally_exploitable=(verdict == "vulnerable"),
        confidence="high",
        verdict=verdict,
        source=source,
        sink_call=sink_call,
        **kw,
    )


def test_both_tracks_vulnerable_merges_high_confidence():
    llm = [_inj("L1", "vulnerable")]
    gn = [_inj("G1", "vulnerable")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "both"
    assert out[0].confidence == "high"
    assert out[0].verdict == "vulnerable"


def test_one_vulnerable_one_safe_or_takes_vulnerable():
    llm = [_inj("L1", "safe")]
    gn = [_inj("G1", "vulnerable")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "both"
    assert out[0].verdict == "vulnerable"


def test_both_safe_stays_safe():
    llm = [_inj("L1", "safe")]
    gn = [_inj("G1", "safe")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "both"
    assert out[0].verdict == "safe"


def test_llm_only_marked_needs_review():
    llm = [_inj("L1", "vulnerable")]
    gn = []
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "llm-only"
    assert out[0].confidence == "needs_review"


def test_gitnexus_only_marked_needs_review():
    llm = []
    gn = [_inj("G1", "vulnerable")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "gitnexus-only"
    assert out[0].confidence == "needs_review"


def test_dedup_key_collapses_same_finding_across_tracks():
    llm = [_inj("L1", "vulnerable", source="q", sink_call="db.exec")]
    gn = [_inj("G1", "safe", source="q", sink_call="db.exec")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].verdict == "vulnerable"


def test_distinct_findings_kept_separately():
    llm = [_inj("L1", "vulnerable", source="q", sink_call="db.exec")]
    gn = [_inj("G1", "vulnerable", source="id", sink_call="os.system")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 2
    sources = {getattr(f, "source") for f in out}
    assert sources == {"q", "id"}


def test_no_verdict_field_uses_externally_exploitable_for_or():
    def _authz(ID, exploitable):
        return AuthzVulnerability(
            ID=ID,
            vulnerability_type="authz",
            externally_exploitable=exploitable,
            confidence="high",
            endpoint="DELETE /api/x",
        )

    llm = [_authz("L1", False)]
    gn = [_authz("G1", True)]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].externally_exploitable is True


def test_union_no_finding_lost():
    llm = [
        _inj("L1", "vulnerable", source="q", sink_call="s1"),
        _inj("L2", "safe", source="w", sink_call="s2"),
    ]
    gn = [
        _inj("G1", "vulnerable", source="q", sink_call="s1"),
        _inj("G2", "vulnerable", source="z", sink_call="s3"),
    ]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 3
