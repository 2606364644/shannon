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


# --- Spec 2026-08-04 authz dual-track ee/dedup ---
# 改动 C: Horizontal 同端点两轨 (location 表述不同) 应按端点合并成 both。
# 改动 B (per-class): authz 三类 (Horizontal/Vertical/Context_Workflow) both 分支
# externally_exploitable 取 OR (兜底 LLM ee 误判); inj/xss/ssrf 保持 "ee 来自 base"
# (上面 test_both_track_vulnerable_keeps_reachability_from_llm_base 锁定)。

def _authz_h(ID, endpoint, externally_exploitable, vuln_code_loc="svc.ts:1", **kw):
    """AuthzVulnerability of type Horizontal (the LLM/GitNexus IDOR overlap class)."""
    return AuthzVulnerability(
        ID=ID,
        vulnerability_type="Horizontal",
        externally_exploitable=externally_exploitable,
        confidence="high",
        endpoint=endpoint,
        vulnerable_code_location=vuln_code_loc,
        **kw,
    )


def test_horizontal_same_endpoint_different_location_merges():
    # 真机根因: LLM 指 service 层 location, GitNexus 指 controller+service location,
    # endpoint 完全一致。按端点去重 → 合并 1 条 both + high。
    llm = [_authz_h("AUTHZ-VULN-03", "GET /api/order/detail", False,
                    vuln_code_loc="service/order.ts:296")]
    gn = [_authz_h("AUTHZ-GN-EXPLORE-03", "GET /api/order/detail", True,
                   vuln_code_loc="controller/order.ts:54 + service/order.ts:296")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "both"
    assert out[0].confidence == "high"


def test_horizontal_both_ee_or_true_when_either_true():
    # per-class OR 兜底: LLM 误判 ee=False, GitNexus 正确 ee=True → 合并 ee=True
    # (authz ee 是 LLM 主观判, prompt 没定义时易误判; 用 GitNexus 纠错)。
    llm = [_authz_h("L1", "GET /api/x", externally_exploitable=False)]
    gn = [_authz_h("G1", "GET /api/x", externally_exploitable=True)]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].externally_exploitable is True


def test_horizontal_both_ee_false_when_both_false():
    llm = [_authz_h("L1", "GET /api/x", externally_exploitable=False)]
    gn = [_authz_h("G1", "GET /api/x", externally_exploitable=False)]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].externally_exploitable is False


def test_vertical_same_endpoint_different_location_not_endpoint_merged():
    # Vertical/Context 只 LLM 产, 不在端点去重范围 → location 不同 → 保持各自条目。
    def _v(ID, loc):
        return AuthzVulnerability(
            ID=ID, vulnerability_type="Vertical", externally_exploitable=True,
            confidence="high", endpoint="POST /api/admin/users",
            vulnerable_code_location=loc,
        )
    llm = [_v("L1", "admin.ts:10")]
    gn = [_v("G1", "admin.ts:20")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 2


def test_horizontal_missing_endpoint_falls_back_to_strict_key():
    # endpoint 缺失 → 不能按端点去重 → fallback 严格 key (location 不同 → 不合并)。
    llm = [AuthzVulnerability(ID="L1", vulnerability_type="Horizontal",
                              externally_exploitable=True, confidence="high",
                              vulnerable_code_location="a.ts:1")]
    gn = [AuthzVulnerability(ID="G1", vulnerability_type="Horizontal",
                             externally_exploitable=True, confidence="high",
                             vulnerable_code_location="b.ts:2")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 2


def test_horizontal_endpoint_normalization_collapses_variants():
    # method 大小写 + trailing slash + query 差异 → 规范化为同一端点 → 合并。
    llm = [_authz_h("L1", "get /api/x/", externally_exploitable=False)]
    gn = [_authz_h("G1", "GET /api/x?foo=bar", externally_exploitable=True)]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "both"
