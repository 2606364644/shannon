from shannon_core.code_index.dual_track_merger import merge_attack_chains


def _chain(name, endpoints, source="llm", confidence="probable"):
    return {
        "name": name,
        "vuln_type": "xss",
        "confidence": confidence,
        "steps": [{"order": i+1, "endpoint": e, "phase": "x", "method": "-", "description": ""}
                  for i, e in enumerate(endpoints)],
        "_source": source,
    }


def test_merge_or_dedup_by_endpoint_sequence():
    """两轨同一 endpoint 序列 → dedup，merge_source=both。"""
    llm = [_chain("llm-xss", ["POST /a", "GET /b"])]
    gn = [_chain("gn-xss", ["POST /a", "GET /b"], source="gitnexus", confidence="confirmed")]
    merged = merge_attack_chains(llm, gn)
    assert len(merged) == 1
    assert merged[0]["merge_source"] == "both"


def test_merge_keeps_disjoint_chains():
    """不重叠的链 → 都保留，各自 llm-only / gitnexus-only。"""
    llm = [_chain("llm-1", ["POST /a", "GET /b"])]
    gn = [_chain("gn-2", ["POST /c", "GET /d"], source="gitnexus")]
    merged = merge_attack_chains(llm, gn)
    assert len(merged) == 2
    sources = {c["merge_source"] for c in merged}
    assert sources == {"llm-only", "gitnexus-only"}


def test_merge_gitnexus_empty_when_unavailable():
    """GitNexus 空 → 全部 llm-only。"""
    llm = [_chain("llm-1", ["POST /a", "GET /b"])]
    merged = merge_attack_chains(llm, [])
    assert len(merged) == 1
    assert merged[0]["merge_source"] == "llm-only"


def test_merge_confidence_promotion_when_gitnexus_higher():
    """同 endpoint 序列下，gn confidence rank 更高 → 覆盖 existing 的 confidence。"""
    llm = [_chain("llm-xss", ["POST /a", "GET /b"], confidence="probable")]
    gn = [_chain("gn-xss", ["POST /a", "GET /b"], source="gitnexus", confidence="confirmed")]
    merged = merge_attack_chains(llm, gn)
    assert len(merged) == 1
    assert merged[0]["merge_source"] == "both"
    assert merged[0]["confidence"] == "confirmed"


def test_merge_keeps_different_vuln_types_same_endpoints():
    """vuln_type 是 sequence key 末位，故 xss 链与 injection 链同路由不合并。"""
    llm = [_chain("llm-xss", ["POST /a", "GET /b"])]
    llm[0]["vuln_type"] = "xss"
    gn = [_chain("gn-inj", ["POST /a", "GET /b"], source="gitnexus")]
    gn[0]["vuln_type"] = "injection"
    merged = merge_attack_chains(llm, gn)
    assert len(merged) == 2
    sources = {c["merge_source"] for c in merged}
    assert sources == {"llm-only", "gitnexus-only"}


def test_merge_output_does_not_contain_internal_keys():
    """内部 _source / _gn_merged 标记不应流到合并输出。"""
    llm = [_chain("llm-xss", ["POST /a", "GET /b"])]
    gn = [_chain("gn-xss", ["POST /a", "GET /b"], source="gitnexus", confidence="confirmed")]
    merged = merge_attack_chains(llm, gn)
    assert len(merged) == 1
    assert "_source" not in merged[0]
    assert "_gn_merged" not in merged[0]
