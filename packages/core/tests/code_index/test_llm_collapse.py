# packages/core/tests/code_index/test_llm_collapse.py
"""LLM 轨条目按接口归并（数据层，spec 用户口径 2026-08-26：多参数不拆卡、
多接口才拆卡）。

黑盒实证（NodeGoat-20260820-135941）：LLM 轨 agent 同接口每参数一条
（INJ-VULN-01/02/03 = POST /contributions 的 preTax/afterTax/roth）→
add_exploit 每 queue ID 一次 → evidence/报告拆 3 卡。归并上移到 merge
activity（SSOT queue 落盘前），渲染/速查表/黑盒 evidence 全下游自动跟随。

仅 taint 三类（injection/xss/ssrf）；auth/authz 直通（missing-control
每条独立漏洞，同接口合并是灾难）。无接口信息不合并（安全优先）。
"""
from supernova_core.code_index.llm_collapse import collapse_llm_entries
from supernova_core.models.queue_schemas import (
    AuthVulnerability,
    InjectionVulnerability,
)


def _llm(id_, param, *, path, severity=None, sink_call=None, title=None):
    """黑盒 NodeGoat 真实形态的 LLM 轨注入条目。"""
    return InjectionVulnerability(
        ID=id_, vulnerability_type="CommandInjection",
        externally_exploitable=True, confidence="high",
        title=title or f"SSJS 注入：POST /contributions 的 {param} 参数直达 eval()",
        source=f"req.body.{param} @ app/routes/contributions.js:32",
        path=path,
        sink_call=sink_call or f"eval() @ app/routes/contributions.js:32 (const {param} = eval(req.body.{param}))",
        verdict="vulnerable", severity=severity, source_track="llm",
    )


def test_same_endpoint_three_params_collapse_to_one():
    """黑盒现场形态：同 POST /contributions 的 preTax/afterTax/roth 3 条 → 1 条。

    主条目 severity 最高者；entries 每参数一行（parameter + 尽力提取的
    sink_location file:line + chain_id 可溯源原条目）；参数并集。
    """
    a = _llm("INJ-VULN-01", "preTax", path="POST /contributions → handler → eval", severity="high")
    b = _llm("INJ-VULN-02", "afterTax",
             path="POST /contributions → isLoggedInMiddleware → ContributionsHandler → eval",
             severity="critical",
             sink_call="eval() @ app/routes/contributions.js:33 (const afterTax = eval(req.body.afterTax))")
    c = _llm("INJ-VULN-03", "roth", path="POST /contributions → x → eval", severity="high")
    out = collapse_llm_entries([a, b, c], "injection")
    assert len(out) == 1
    m = out[0]
    assert m.ID == "INJ-VULN-02"            # severity 最高（critical）为主条目
    assert m.severity == "critical"
    assert set(m.affected_parameters) == {"preTax", "afterTax", "roth"}
    entries = {(e["parameter"], e["sink_location"], e["chain_id"])
               for e in m.affected_entries}
    assert ("preTax", "app/routes/contributions.js:32", "INJ-VULN-01") in entries
    assert ("afterTax", "app/routes/contributions.js:33", "INJ-VULN-02") in entries
    assert ("roth", "app/routes/contributions.js:32", "INJ-VULN-03") in entries


def test_different_endpoints_stay_separate():
    a = _llm("INJ-VULN-01", "preTax", path="POST /contributions → eval")
    b = _llm("INJ-VULN-04", "threshold",
             path="GET /allocations/:userId → $where", severity="high")
    out = collapse_llm_entries([a, b], "injection")
    assert len(out) == 2                    # 多接口才拆卡


def test_no_endpoint_info_no_merge():
    """接口信息全无（旧数据）→ 不合并（无 key 不能安全合）。"""
    a = _llm("INJ-VULN-01", "preTax", path="handler → eval")
    b = _llm("INJ-VULN-02", "afterTax", path="handler → eval")
    out = collapse_llm_entries([a, b], "injection")
    assert len(out) == 2


def test_auth_classes_pass_through():
    """auth/authz 直通：同接口的 missing-control 是不同漏洞，绝不合并。"""
    a = AuthVulnerability(
        ID="AUTH-VULN-01", vulnerability_type="abuse_defenses_missing",
        externally_exploitable=True, confidence="high",
        source_endpoint="POST /login", title="无速率限制")
    b = AuthVulnerability(
        ID="AUTH-VULN-02", vulnerability_type="session_fixation",
        externally_exploitable=True, confidence="high",
        source_endpoint="POST /login", title="会话不轮换")
    out = collapse_llm_entries([a, b], "auth")
    assert len(out) == 2


def test_endpoints_field_with_role_note_is_stripped():
    """T2 新 endpoints 字段（"POST /memos (write)"）→ 剥注记后作归并 key。"""
    a = InjectionVulnerability(
        ID="XSS-VULN-01", vulnerability_type="Stored",
        externally_exploitable=True, confidence="high",
        endpoints=["POST /memos (write)", "GET /memos (trigger)"],
        affected_parameters=["memo (body)"], title="存储型 XSS")
    b = InjectionVulnerability(
        ID="XSS-VULN-02", vulnerability_type="Stored",
        externally_exploitable=True, confidence="high",
        endpoints=["POST /memos (write)"],
        affected_parameters=["memo"], title="存储型 XSS（重复表述）")
    out = collapse_llm_entries([a, b], "xss")
    assert len(out) == 1
    # 参数名剥角色注记后归一（"memo (body)" ≙ "memo" = 同一参数）
    assert set(out[0].affected_parameters) == {"memo"}
    # endpoints 并集去重（注记差异视为同接口）
    assert {e for e in out[0].endpoints} == {"POST /memos", "GET /memos"}
