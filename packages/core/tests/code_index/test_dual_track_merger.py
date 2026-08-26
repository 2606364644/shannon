from supernova_core.code_index.dual_track_merger import (
    merge_dual_track_queues,
)
from supernova_core.models.queue_schemas import (
    AuthVulnerability,
    AuthzVulnerability,
    InjectionVulnerability,
    XssVulnerability,
)


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


# --- spec 2026-08-26 §5.7：chain_verdict 失败显式化（unadjudicated 不被吞）---

def test_gitnexus_only_unadjudicated_confidence_survives_merge():
    """GN-only 卡 confidence="unadjudicated"（chain_verdict 判定通道失败）→
    merge 不得覆写成 needs_review（静默混入待复核）；普通 gn-only 仍 needs_review。"""
    unadj = InjectionVulnerability(
        ID="G1", vulnerability_type="injection", externally_exploitable=True,
        confidence="unadjudicated", verdict="vulnerable",
        source="q", sink_call="db.exec")
    normal = _inj("G2", "vulnerable", source="q2", sink_call="db.exec2")
    out = merge_dual_track_queues([], [unadj, normal], mode="verdict")
    assert len(out) == 2
    by_id = {f.ID: f for f in out}
    assert by_id["G1"].confidence == "unadjudicated"
    assert by_id["G2"].confidence == "needs_review"


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


# --- LLM 辅助跨轨配对归并（spec 2026-08-26 §6.1）---

import json as _json

from supernova_core.code_index.dual_track_merger import (
    apply_pairing_merge,
    build_pairing_prompt,
    parse_pairing_response,
)


def _llm_card(ID="XSS-VULN-01", **kw):
    return InjectionVulnerability(
        ID=ID, vulnerability_type="Stored XSS",
        externally_exploitable=True, confidence="medium",
        verdict="vulnerable", title="Stored XSS: memo rendered without encoding",
        endpoint="POST /memos", sink_function="marked(doc.memo)",
        severity="high", affected_parameters=["memo"],
        **kw)


def _gn_card(ID="XSS-GN-13", **kw):
    return InjectionVulnerability(
        ID=ID, vulnerability_type="xss",
        externally_exploitable=True, confidence="low",
        verdict="vulnerable", source="memo (app/routes/memos.js:MemosHandler:6)",
        sink_call="app/routes/memos.js:MemosHandler:render:27:19",
        affected_entries=[{"parameter": "memo",
                            "sink_location": "app/routes/memos.js:27",
                            "chain_id": ID, "track": "gitnexus"}],
        severity="medium", **kw)


def test_build_pairing_prompt_contains_summaries():
    prompt = build_pairing_prompt([_llm_card()], [_gn_card()])
    assert "XSS-VULN-01" in prompt and "XSS-GN-13" in prompt
    assert "marked(doc.memo)" in prompt and "POST /memos" in prompt
    # JSON 输出契约（§5.1 升级：{"merge":[...]}，含 mode=merge|attach 两种形态）
    assert '"merge"' in prompt and '"mode"' in prompt


def test_parse_pairing_response_valid():
    raw = _json.dumps({"pairs": [
        {"gn_id": "G1", "llm_id": "L1", "confidence": "high", "reason": "same sink"},
        {"gn_id": "G2", "llm_id": "L2", "confidence": "medium"},
    ]})
    pairs = parse_pairing_response(raw, {"G1", "G2"}, {"L1", "L2"})
    assert [(p.gn_id, p.llm_id, p.confidence) for p in pairs] == [
        ("G1", "L1", "high"), ("G2", "L2", "medium")]


def test_parse_pairing_drops_unknown_ids_and_bad_confidence():
    raw = _json.dumps({"pairs": [
        {"gn_id": "GHOST", "llm_id": "L1", "confidence": "high"},   # 幻觉 gn_id
        {"gn_id": "G1", "llm_id": "GHOST2", "confidence": "high"},  # 幻觉 llm_id
        {"gn_id": "G1", "llm_id": "L1", "confidence": "certain"},   # 非法置信度
        {"gn_id": "G2", "llm_id": "L2"},                             # 缺 confidence
    ]})
    assert parse_pairing_response(raw, {"G1", "G2"}, {"L1", "L2"}) == []


def test_parse_pairing_unparseable_returns_empty():
    assert parse_pairing_response("not json at all", {"G1"}, {"L1"}) == []
    assert parse_pairing_response("", {"G1"}, {"L1"}) == []


def test_apply_pairing_high_confidence_merges():
    llm = _llm_card()
    gn = _gn_card()
    merged = merge_dual_track_queues([llm], [], mode="verdict") \
        + merge_dual_track_queues([], [gn], mode="verdict")
    from supernova_core.code_index.dual_track_merger import TrackPair
    out = apply_pairing_merge(merged, [TrackPair("XSS-GN-13", "XSS-VULN-01", "high")])
    assert len(out) == 1
    card = out[0]
    assert card.ID == "XSS-VULN-01"
    assert card.merge_source == "both"
    assert card.confidence == "high"
    # GN entries 并入 + severity 取高
    assert any(e.get("chain_id") == "XSS-GN-13" for e in card.affected_entries)
    assert card.severity == "high"


def test_apply_pairing_medium_keeps_separate():
    llm, gn = _llm_card(), _gn_card()
    merged = merge_dual_track_queues([llm], [], mode="verdict") \
        + merge_dual_track_queues([], [gn], mode="verdict")
    from supernova_core.code_index.dual_track_merger import TrackPair
    out = apply_pairing_merge(merged, [TrackPair("XSS-GN-13", "XSS-VULN-01", "medium")])
    assert len(out) == 2  # 中低置信不合并（误合并比漏合并更伤可信度）


def test_apply_pairing_missing_card_skipped():
    merged = merge_dual_track_queues([_llm_card()], [], mode="verdict")
    from supernova_core.code_index.dual_track_merger import TrackPair
    out = apply_pairing_merge(merged, [TrackPair("NOPE-GN", "XSS-VULN-01", "high")])
    assert len(out) == 1 and out[0].merge_source == "llm-only"


# --- spec 2026-08-26 §5.1 ①归并终审：配对输出两种形态（merge 成 both / attach 挂靠）---

def test_build_pairing_prompt_teaches_merge_and_attach_modes():
    """prompt 明确教两种输出形态：mode=merge（both 字段融合）/ mode=attach
    （LLM 卡为主体、GN 卡 ID 挂靠 merged_from、不再独立出现）。"""
    prompt = build_pairing_prompt([_llm_card()], [_gn_card()])
    assert '"merge"' in prompt or '"merge":' in prompt
    assert "attach" in prompt
    assert "merged_from" in prompt
    assert "merge|attach" in prompt or '"merge"|"attach"' in prompt


def test_parse_pairing_new_merge_form_with_modes():
    """新输出形态 {"merge":[{llm_id,gn_id,mode,confidence}]}：mode 解析；
    缺 mode → merge（向后兼容）；非法 mode / 幻觉 ID / 非法 confidence → 整对丢弃。"""
    raw = _json.dumps({"merge": [
        {"llm_id": "L1", "gn_id": "G1", "mode": "attach", "confidence": "high"},
        {"llm_id": "L2", "gn_id": "G2", "mode": "merge", "confidence": "high"},
        {"llm_id": "L3", "gn_id": "G3", "confidence": "high"},           # 缺 mode → merge
        {"llm_id": "L4", "gn_id": "G4", "mode": "fuse", "confidence": "high"},   # 非法 mode
        {"llm_id": "GHOST", "gn_id": "G5", "mode": "attach", "confidence": "high"},  # 幻觉 llm_id
        {"llm_id": "L5", "gn_id": "G5", "mode": "attach"},               # 缺 confidence
    ]})
    pairs = parse_pairing_response(raw, {"G1", "G2", "G3", "G4", "G5"},
                                   {"L1", "L2", "L3", "L4", "L5"})
    assert [(p.llm_id, p.gn_id, p.mode, p.confidence) for p in pairs] == [
        ("L1", "G1", "attach", "high"),
        ("L2", "G2", "merge", "high"),
        ("L3", "G3", "merge", "high"),
    ]


def test_parse_pairing_legacy_pairs_form_still_supported():
    """旧 {"pairs":[...]} 形态继续可解析（mode 缺省 merge）——向后兼容。"""
    raw = _json.dumps({"pairs": [
        {"gn_id": "G1", "llm_id": "L1", "confidence": "high"}]})
    pairs = parse_pairing_response(raw, {"G1"}, {"L1"})
    assert len(pairs) == 1 and pairs[0].mode == "merge"


def _stub_two_track_merged():
    """LLM-only 卡 + GN-only 卡（确定性 key 配不上的同洞：sink 文件/行失配）。"""
    llm = InjectionVulnerability(
        ID="XSS-VULN-01", vulnerability_type="Stored XSS",
        externally_exploitable=True, confidence="medium", verdict="vulnerable",
        title="Stored XSS: memo rendered without encoding",
        endpoint="POST /memos", sink_function="res.render('memos', {memo})",
        severity="high", affected_parameters=["memo"])
    gn = InjectionVulnerability(
        ID="XSS-GN-13", vulnerability_type="xss",
        externally_exploitable=True, confidence="unadjudicated", verdict="vulnerable",
        source="memo (app/routes/memos.js:MemosHandler:6)",
        sink_call="app/routes/memos.js:MemosHandler:render:27:19",
        affected_entries=[{"parameter": "memo",
                           "sink_location": "app/routes/memos.js:27",
                           "chain_id": "XSS-GN-13", "track": "gitnexus"}],
        severity="medium", source_track="gitnexus")
    return (merge_dual_track_queues([llm], [], mode="verdict")
            + merge_dual_track_queues([], [gn], mode="verdict"))


def test_apply_pairing_attach_records_merged_from_and_removes_gn():
    """mode=attach：LLM 卡为主体，GN 卡 ID 写入 merged_from，GN 卡从列表移除。"""
    from supernova_core.code_index.dual_track_merger import TrackPair
    out = apply_pairing_merge(
        _stub_two_track_merged(),
        [TrackPair("XSS-GN-13", "XSS-VULN-01", "high", mode="attach")])
    assert len(out) == 1
    card = out[0]
    assert card.ID == "XSS-VULN-01"
    assert card.merged_from == ["XSS-GN-13"]
    assert "XSS-GN-13" not in [f.ID for f in out]   # GN 卡不再独立出现


def test_apply_pairing_attach_preserves_subject_card_fields():
    """attach 是呈现层挂靠：主体卡 merge_source/confidence/verdict/severity/
    affected_entries 全不变（不冒充 both/high，不改双轨判定结果——spec §8）。"""
    from supernova_core.code_index.dual_track_merger import TrackPair
    out = apply_pairing_merge(
        _stub_two_track_merged(),
        [TrackPair("XSS-GN-13", "XSS-VULN-01", "high", mode="attach")])
    card = out[0]
    assert card.merge_source == "llm-only"
    assert card.confidence == "needs_review"     # 主体卡原置信度（非 both 的 high）
    assert card.verdict == "vulnerable"
    assert card.severity == "high"               # 不与 GN 取高（非字段融合）
    assert not card.affected_entries             # GN entries 不并入（非 both 融合）


def test_apply_pairing_attach_appends_to_existing_merged_from():
    """主体卡已有 merged_from → 追加不覆盖、不重复。"""
    from supernova_core.code_index.dual_track_merger import TrackPair
    merged = _stub_two_track_merged()
    merged[0].merged_from = ["XSS-GN-99"]
    out = apply_pairing_merge(
        merged, [TrackPair("XSS-GN-13", "XSS-VULN-01", "high", mode="attach"),
                 TrackPair("XSS-GN-13", "XSS-VULN-01", "high", mode="attach")])
    assert out[0].merged_from == ["XSS-GN-99", "XSS-GN-13"]


def test_apply_pairing_mixed_modes_in_one_batch():
    """一次配对同时含 merge 与 attach 两种形态：各自生效，GN 卡各只消费一次。"""
    from supernova_core.code_index.dual_track_merger import TrackPair
    llm1 = InjectionVulnerability(
        ID="XSS-VULN-01", vulnerability_type="Stored XSS",
        externally_exploitable=True, confidence="medium", verdict="vulnerable",
        endpoint="POST /memos", sink_function="res.render('memos')")
    llm2 = InjectionVulnerability(
        ID="XSS-VULN-02", vulnerability_type="Stored XSS",
        externally_exploitable=True, confidence="medium", verdict="vulnerable",
        endpoint="POST /research", sink_function="res.render('research')")
    gn1 = InjectionVulnerability(
        ID="XSS-GN-13", vulnerability_type="xss", externally_exploitable=True,
        confidence="low", verdict="vulnerable", source_track="gitnexus",
        sink_call="app/routes/memos.js:MemosHandler:render:27:19")
    gn2 = InjectionVulnerability(
        ID="XSS-GN-14", vulnerability_type="xss", externally_exploitable=True,
        confidence="low", verdict="vulnerable", source_track="gitnexus",
        sink_call="app/routes/research.js:ResearchHandler:render:31:9")
    merged = (merge_dual_track_queues([llm1, llm2], [], mode="verdict")
              + merge_dual_track_queues([], [gn1, gn2], mode="verdict"))
    out = apply_pairing_merge(merged, [
        TrackPair("XSS-GN-13", "XSS-VULN-01", "high", mode="merge"),
        TrackPair("XSS-GN-14", "XSS-VULN-02", "high", mode="attach"),
    ])
    assert len(out) == 2
    by_id = {f.ID: f for f in out}
    assert by_id["XSS-VULN-01"].merge_source == "both"          # merge 形态
    assert by_id["XSS-VULN-01"].merged_from is None
    assert by_id["XSS-VULN-02"].merge_source == "llm-only"      # attach 形态
    assert by_id["XSS-VULN-02"].merged_from == ["XSS-GN-14"]


def test_apply_pairing_attach_medium_confidence_not_applied():
    """attach 同样受 high 置信门禁（误挂靠也吞 GN 卡，保守优先）。"""
    from supernova_core.code_index.dual_track_merger import TrackPair
    out = apply_pairing_merge(
        _stub_two_track_merged(),
        [TrackPair("XSS-GN-13", "XSS-VULN-01", "medium", mode="attach")])
    assert len(out) == 2
    assert out[0].merged_from is None and out[1].ID == "XSS-GN-13"


def test_apply_pairing_attach_missing_cards_skipped():
    """幻觉 ID（卡不在列表）→ 跳过，原列表不变。"""
    from supernova_core.code_index.dual_track_merger import TrackPair
    out = apply_pairing_merge(
        _stub_two_track_merged(),
        [TrackPair("GHOST-GN", "XSS-VULN-01", "high", mode="attach"),
         TrackPair("XSS-GN-13", "GHOST-VULN", "high", mode="attach")])
    assert len(out) == 2
    assert all(f.merged_from is None for f in out)
