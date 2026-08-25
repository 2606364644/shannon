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


# --- 漏洞 title 字段三分支流转（spec 2026-08-06）---
# merger 经 model_dump→model_validate 透传 title，无需改逻辑。base=both/llm-only
# 取 LLM 轨 title；gitnexus-only 取 GitNexus 轨 title。

def test_both_branch_keeps_llm_track_title():
    """both 分支 base=llm → title 取 LLM 轨 title。"""
    llm = [_inj("L1", "vulnerable", source="q", sink_call="db.exec",
                title="LLM SQLi title")]
    gn = [_inj("G1", "vulnerable", source="q", sink_call="db.exec",
               title="GN SQLi title")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "both"
    assert out[0].title == "LLM SQLi title"


def test_llm_only_branch_keeps_llm_track_title():
    llm = [_inj("L1", "vulnerable", title="LLM-only title")]
    out = merge_dual_track_queues(llm, [], mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "llm-only"
    assert out[0].title == "LLM-only title"


def test_gitnexus_only_branch_keeps_gitnexus_track_title():
    gn = [_inj("G1", "vulnerable", title="GN-only title")]
    out = merge_dual_track_queues([], gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "gitnexus-only"
    assert out[0].title == "GN-only title"


# --- Task 3（spec 2026-08-25 §3.3）：merger 单位 key + GN 收敛接线 + both 并集 ---

def _llm_inj(**kw):
    base = dict(ID="INJ-VULN-01", vulnerability_type="injection",
                externally_exploitable=True, confidence="high",
                title="命令注入：POST /contributions 直接 eval()（RCE）",
                source="preTax & req.body",
                path="POST /contributions → handleContributionsUpdate → eval(req.body.preTax)",
                sink_function="eval", verdict="vulnerable", severity="high",
                affected_entries=[{"parameter": "preTax",
                                   "sink_location": "app/routes/contributions.js:32",
                                   "chain_id": None, "track": "llm"}])
    base.update(kw)
    return InjectionVulnerability(**base)

def _gn_inj(id_, param, line):
    return InjectionVulnerability(
        ID=id_, vulnerability_type="injection", externally_exploitable=True,
        confidence="low", source=f"{param} (app/routes/contributions.js:ContributionsHandler:7)",
        path="POST /contributions → chain",
        sink_call=f"app/routes/contributions.js:ContributionsHandler:eval:{line}:{line}",
        verdict="vulnerable", source_track="gitnexus")

def test_merger_collapses_and_cross_track_dedup():
    """9 条 GN 同单位收敛后与 LLM 轨同单位条目合并为 1 条 both。"""
    llm = [_llm_inj()]
    gn = [_gn_inj(f"INJ-GN-{i:02d}", p, ln)
          for i, (p, ln) in enumerate([(p, ln) for p in ("preTax", "afterTax", "roth")
                                       for ln in (32, 33, 34)], start=1)]
    merged = merge_dual_track_queues(llm, gn)
    assert len(merged) == 1
    m = merged[0]
    assert m.merge_source == "both"
    assert m.ID == "INJ-VULN-01"            # LLM 轨为 base（叙述权威）
    assert m.severity == "critical"          # GN 兜底 eval=critical 取高
    # LLM 1 行 + GN 9 行并集：LLM 行 (preTax, contributions.js:32) 与 GN 收敛
    # 9 行中第 1 行同 (parameter, sink_location) 重复 → 去重后 9 行（brief 原文
    # 写 10 未计该重复；Step 3 合并策略按 (parameter, sink_location) 去重为准）。
    assert len(m.affected_entries) == 9
    assert set(m.affected_parameters) == {"preTax", "afterTax", "roth"}

def test_merger_keeps_different_endpoint_separate():
    llm = [_llm_inj()]
    gn = [_gn_inj("INJ-GN-01", "memo", 27)]
    gn[0].path = "GET /memos → chain"
    gn[0].sink_call = "app/routes/memos.js:MemosHandler:render:27:19"
    merged = merge_dual_track_queues(llm, gn)
    assert len(merged) == 2                  # 不同接口不合并


# --- F4（spec 2026-08-25 终审）：跨轨 sink 限定名失配 → under-merge ---
# LLM 轨 sink_function 可写 `cursor.execute`，GN 轨 sink_call id 解析出裸名
# `execute`——同一漏洞单位因带不带 receiver 永不合键。归一化后须落同一把 key。

def test_llm_qualified_sink_merges_with_gn_bare_sink_name():
    """LLM `cursor.execute` + GN `...:execute:32:10` → 同单位合并 1 条 both。"""
    llm = [InjectionVulnerability(
        ID="INJ-VULN-01", vulnerability_type="injection",
        externally_exploitable=True, confidence="high",
        endpoint="POST /api/x", sink_function="cursor.execute",
        verdict="vulnerable")]
    gn = [InjectionVulnerability(
        ID="INJ-GN-01", vulnerability_type="injection",
        externally_exploitable=True, confidence="low",
        path="POST /api/x", sink_call="repo/db.py:Handler:execute:32:10",
        verdict="vulnerable", source_track="gitnexus")]
    out = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(out) == 1
    assert out[0].merge_source == "both"


def test_sink_name_case_variants_still_merge():
    """大写变体（GN 函数段 `Execute` / LLM `Cursor.Execute`）归一化后同样合并。"""
    variants = [
        ("cursor.execute", "repo/db.py:Handler:Execute:32:10"),  # GN 函数段大写
        ("Cursor.Execute", "repo/db.py:Handler:execute:32:10"),  # LLM receiver+函数大写
    ]
    for llm_sink, gn_sink_call in variants:
        llm = [InjectionVulnerability(
            ID="INJ-VULN-01", vulnerability_type="injection",
            externally_exploitable=True, confidence="high",
            endpoint="POST /api/x", sink_function=llm_sink,
            verdict="vulnerable")]
        gn = [InjectionVulnerability(
            ID="INJ-GN-01", vulnerability_type="injection",
            externally_exploitable=True, confidence="low",
            path="POST /api/x", sink_call=gn_sink_call,
            verdict="vulnerable", source_track="gitnexus")]
        out = merge_dual_track_queues(llm, gn, mode="verdict")
        assert len(out) == 1, (llm_sink, gn_sink_call)
        assert out[0].merge_source == "both", (llm_sink, gn_sink_call)
