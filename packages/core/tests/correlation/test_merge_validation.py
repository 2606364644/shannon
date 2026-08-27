"""合并层确定性校验与拼装（spec 2026-08-27 §6）——零推断，防幻觉与结构性拼装。

- validate_vuln_refs：vuln_id 不在对应 service queue 的 ID 集 → 标 invalid_ref（不删）
- assemble_multi_hop_chains：边邻接启发拼多跳链（basis/confidence 显式标注）
- sanitize_adjudication_cards：direction 与 conclusion 矛盾 → 拦下标 needs-review
"""
import copy

from supernova_core.correlation.merge_validation import (
    assemble_multi_hop_chains, sanitize_adjudication_cards, validate_vuln_refs,
)


def _edge(f, t, flows=None, calls=None):
    return {"from": f, "to": t, "protocol": "grpc", "status": "ok",
            "calls": calls or [], "flows": flows or []}


def _flow(**over):
    base = {"entry": "POST /x", "method": "svc/M", "call_site": {"file": "a.ts", "line": 1, "snippet": "s"},
            "vuln_refs": [], "confidence": "high", "evidence": "e"}
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# validate_vuln_refs
# ---------------------------------------------------------------------------

def test_valid_vuln_id_not_flagged():
    edges = [_edge("g", "b", flows=[_flow(vuln_refs=[
        {"vuln_id": "INJ-001", "service": "b", "source": "queue"}])])]
    out = validate_vuln_refs(copy.deepcopy(edges), {"b": {"INJ-001"}})
    assert "invalid_ref" not in out[0]["flows"][0]["vuln_refs"][0]


def test_invalid_vuln_id_flagged_not_deleted():
    edges = [_edge("g", "b", flows=[_flow(vuln_refs=[
        {"vuln_id": "INJ-999", "service": "b", "source": "queue"}])])]
    out = validate_vuln_refs(edges, {"b": {"INJ-001"}})
    ref = out[0]["flows"][0]["vuln_refs"][0]
    assert ref["invalid_ref"] is True        # 透明标注
    assert ref["vuln_id"] == "INJ-999"       # 不删


def test_missing_vuln_id_not_flagged():
    """agent-discovered 的 ref 无 vuln_id——不是幻觉引用，不标。"""
    edges = [_edge("g", "b", flows=[_flow(vuln_refs=[
        {"service": "b", "source": "agent-discovered"}])])]
    out = validate_vuln_refs(edges, {"b": {"INJ-001"}})
    assert "invalid_ref" not in out[0]["flows"][0]["vuln_refs"][0]


def test_unknown_service_tolerated():
    """service 不在 ID 集映射里（如 from 仓无 queue）——容错不标。"""
    edges = [_edge("g", "b", flows=[_flow(vuln_refs=[
        {"vuln_id": "INJ-001", "service": "nope", "source": "queue"}])])]
    out = validate_vuln_refs(edges, {"b": {"INJ-001"}})
    assert "invalid_ref" not in out[0]["flows"][0]["vuln_refs"][0]


# ---------------------------------------------------------------------------
# assemble_multi_hop_chains
# ---------------------------------------------------------------------------

def test_two_hop_chain_assembled_with_basis_labels():
    edges = [
        _edge("gateway", "order", flows=[_flow()]),                 # 攻击链到达 order
        _edge("order", "payment", calls=[{"method": "pay/Charge",
                                          "call_site": {"file": "o.go", "line": 2, "snippet": "c"},
                                          "confidence": "high", "evidence": "e"}]),
    ]
    chains = assemble_multi_hop_chains(edges)
    assert len(chains) == 1
    c = chains[0]
    assert c["path"] == ["gateway", "order", "payment"]
    assert c["basis"] == "edge-adjacency"
    assert c["confidence"] == "structural"


def test_no_flow_no_chain():
    """首边无 flows（无攻击链到达 to）→ 不成多跳链。"""
    edges = [
        _edge("gateway", "order"),
        _edge("order", "payment", calls=[{"method": "m", "call_site": {"file": "f", "line": 1, "snippet": "s"},
                                          "confidence": "low", "evidence": "e"}]),
    ]
    assert assemble_multi_hop_chains(edges) == []


def test_no_calls_no_chain():
    """邻接边无 calls → 无调用证据，不成链。"""
    edges = [
        _edge("gateway", "order", flows=[_flow()]),
        _edge("order", "payment", calls=[]),
    ]
    assert assemble_multi_hop_chains(edges) == []


def test_cycle_terminated():
    """环（order→payment→order）不死循环，产出有限链集。"""
    edges = [
        _edge("gateway", "order", flows=[_flow()]),
        _edge("order", "payment", calls=[{"method": "m", "call_site": {"file": "f", "line": 1, "snippet": "s"},
                                          "confidence": "high", "evidence": "e"}]),
        _edge("payment", "order", calls=[{"method": "m2", "call_site": {"file": "f", "line": 2, "snippet": "s"},
                                          "confidence": "high", "evidence": "e"}]),
    ]
    chains = assemble_multi_hop_chains(edges)
    for c in chains:
        assert len(c["path"]) == len(set(c["path"]))   # 无重复节点
    assert all(len(c["path"]) <= 3 for c in chains)     # 有界


# ---------------------------------------------------------------------------
# sanitize_adjudication_cards
# ---------------------------------------------------------------------------

def _card(direction, conclusion):
    return {"direction": direction, "conclusion": conclusion,
            "finding_ref": {"service": "b", "vuln_id": "X", "origin": "queue"},
            "cross_service_context": "", "analysis_process": [],
            "verification_evidence": [], "reasoning": "", "confidence": "low"}


def test_contradiction_intercepted_to_needs_review():
    cards = [_card("upgrade", "not-vulnerable"),   # 翻案却判非漏洞——矛盾
             _card("confirm", "not-vulnerable")]   # 确认却判非漏洞——矛盾
    out = sanitize_adjudication_cards(cards)
    assert all(c["conclusion"] == "needs-review" for c in out)


def test_consistent_cards_untouched():
    cards = [_card("confirm", "vulnerable"),
             _card("upgrade", "vulnerable"),
             _card("downgrade", "downgraded"),
             _card("downgrade", "not-vulnerable"),
             _card("maintain", "not-vulnerable")]
    out = sanitize_adjudication_cards(cards)
    assert [c["conclusion"] for c in out] == [
        "vulnerable", "vulnerable", "downgraded", "not-vulnerable", "not-vulnerable"]


def test_unknown_direction_tolerated():
    out = sanitize_adjudication_cards([_card("weird", "vulnerable")])
    assert out[0]["conclusion"] == "vulnerable"
