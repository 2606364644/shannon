from supernova_core.code_index.dual_track_merger import merge_dual_track_queues
from supernova_core.models.queue_schemas import AuthzVulnerability, InjectionVulnerability


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
    # When a vuln class has no `verdict` field (e.g. AuthzVulnerability),
    # `_is_vulnerable` falls back to `externally_exploitable` to compute the
    # verdict-OR. The OR still happens (the two same-key findings collapse into
    # one `merge_source="both"` entry). What NO LONGER happens: the merged
    # finding's `externally_exploitable` being recomputed from the verdict
    # (spec 改动 3′ — it is a reachability tag, preserved from the base).
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
    # OR still merged the two tracks into one both-source finding.
    assert out[0].merge_source == "both"
    # Reachability is preserved from the base (LLM) finding, not overwritten.
    assert out[0].externally_exploitable is False


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


# --- Spec 改动 3′: externally_exploitable is a reachability tag, not verdict ---
# (true = public-internet reachable; false = internal / cross-service).
# The merger must NOT overwrite it with the verdict-OR result.

def _reachability_inj(externally_exploitable, verdict="vulnerable", confidence="high"):
    """InjectionVulnerability where externally_exploitable is decoupled from verdict
    (a cross-service finding: verdict=vulnerable but externally_exploitable=False)."""
    return InjectionVulnerability(
        ID="INJ-1",
        vulnerability_type="injection",
        externally_exploitable=externally_exploitable,
        confidence=confidence,
        verdict=verdict,
    )


def test_cross_service_reachability_preserved_through_merge():
    # Cross-service finding: externally_exploitable=False, verdict=vulnerable.
    # LLM-only branch — base is the LLM finding; its reachability tag must survive.
    llm = [_reachability_inj(externally_exploitable=False, verdict="vulnerable")]
    merged = merge_dual_track_queues(llm, [], mode="verdict")
    assert len(merged) == 1
    assert merged[0].externally_exploitable is False, (
        "reachability tag must not be overwritten by the verdict-OR result"
    )
    assert merged[0].verdict == "vulnerable"


def test_both_track_vulnerable_keeps_reachability_from_llm_base():
    # both-vulnerable branch — base is the LLM finding (dual_track_merger.py:100),
    # so its reachability (False) is authoritative, even when GitNexus says True.
    llm = [_reachability_inj(externally_exploitable=False, verdict="vulnerable")]
    gn = [_reachability_inj(externally_exploitable=True, verdict="vulnerable")]
    merged = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(merged) == 1
    assert merged[0].externally_exploitable is False
    assert merged[0].verdict == "vulnerable"  # verdict is still the OR result
