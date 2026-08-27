"""P4 组装器 fixture 矩阵（spec 2026-08-20 §3 聚合规则 × §6 降级矩阵）。"""
import json
from pathlib import Path

import pytest


def _write_intermediate(d: Path, name: str, obj):
    (d / "intermediate").mkdir(exist_ok=True)
    (d / "intermediate" / name).write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture
def deliverables(tmp_path: Path) -> Path:
    (tmp_path / "intermediate").mkdir()
    return tmp_path


def test_dual_track_full(deliverables: Path):
    """GitNexus 枝（verdict from chain_verdicts）+ LLM 枝（dataflow_steps）同树。"""
    _write_intermediate(deliverables, "injection_chain_verdicts.json", {"verdicts": [
        {"flow_id": "u1->s1", "sink_call_site_id": "s1", "verdict": "vulnerable",
         "reason": "", "sanitizer_annotations": [], "confidence": "high", "vuln_class": "injection"},
    ]})
    _write_intermediate(deliverables, "parameter_graph.json", {"taint_flows": [{
        "flow_id": "u1->s1", "entry_point_id": "ep1", "source_param": "name",
        "source_type": "query", "sink_call_site_id": "s1",
        "propagation_steps": [
            {"from_func_id": "ep1", "to_func_id": "ctrl", "code_location": "c.js:25",
             "transformation": "concat", "intermediate_vars": ["q"]},
        ],
    }]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [{"id": "c.js:ctrl:25", "file_path": "c.js", "function_name": "ctrl",
                    "start_line": 20, "end_line": 30, "source_code": "\n".join(f"l{i}" for i in range(20, 31))}],
        "sink_call_sites": [{"id": "s1", "callee_name": "execute", "category": "SQL",
                             "rule_id": "py-sql-raw", "file_path": "app/db.py", "line": 42, "column": 0}],
        "source_points": [],
    })
    _write_intermediate(deliverables, "injection_exploitation_queue.json", {"vulnerabilities": [
        {"ID": "INJ-VULN-01", "vulnerability_type": "injection", "externally_exploitable": True,
         "confidence": "high", "merge_source": "both", "title": "sqli",
         "dataflow_steps": [{"label": "ctrl", "file": "c.js", "line": 25, "protection": None}]},
    ]})
    _write_intermediate(deliverables, "injection_safe_vectors.json", {"vectors": []})

    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    assert view is not None
    assert view["schema_version"] == 1
    assert view["summary"]["vulnerable_sinks"] >= 1
    tree = view["trees"][0]
    assert tree["sink"]["label"] == "execute"
    tracks = {b["track"] for b in tree["branches"]}
    assert tracks == {"gitnexus", "llm"}
    gn = next(b for b in tree["branches"] if b["track"] == "gitnexus")
    assert gn["verdict"] == "vulnerable"
    assert len(gn["nodes"]) == 1


def test_all_products_missing_returns_none(deliverables: Path):
    """全部产物缺 → None（不产文件）。"""
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    assert assemble_dataflow_view(deliverables) is None


def test_safe_only_tree_has_empty_findings(deliverables: Path):
    """safe 枝也进树 → safe-only 树 findings=[]。"""
    _write_intermediate(deliverables, "injection_chain_verdicts.json", {"verdicts": [
        {"flow_id": "u2->s2", "sink_call_site_id": "s2", "verdict": "safe",
         "reason": "shlex.quote", "sanitizer_annotations": [], "confidence": "high", "vuln_class": "injection"},
    ]})
    _write_intermediate(deliverables, "parameter_graph.json", {"taint_flows": [
        {"flow_id": "u2->s2", "sink_call_site_id": "s2", "source_param": "q", "source_type": "query",
         "propagation_steps": [{"code_location": "h.js:22", "transformation": "sanitize_hint:shlex.quote"}]}]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [], "sink_call_sites": [{"id": "s2", "callee_name": "exec", "category": "COMMAND",
                                          "rule_id": "r", "file_path": "d.py", "line": 5, "column": 0}], "source_points": []})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    tree = next(t for t in view["trees"] if t["sink"]["line"] == 5)
    assert tree["findings"] == []
    safe_branch = tree["branches"][0]
    assert safe_branch["verdict"] == "safe"


def test_control_findings_authz(deliverables: Path):
    """authz → control_findings 关卡链。"""
    _write_intermediate(deliverables, "authz_exploitation_queue.json", {"vulnerabilities": [
        {"ID": "AUTHZ-01", "vulnerability_type": "authz", "endpoint": "PUT /api/orders/:id",
         "externally_exploitable": True, "confidence": "high",
         "guard_evidence": "无 owner 检查", "missing_defense": "owner check",
         "vulnerable_code_location": "c.js:40"}]})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    cf = view["control_findings"][0]
    assert cf["endpoint"] == "PUT /api/orders/:id"
    assert cf["chain"][0]["status"] == "missing"


def test_code_snippet_volume_control(deliverables: Path):
    """纯透传步 has_code:false；有故事的步 has_code:true。"""
    _write_intermediate(deliverables, "parameter_graph.json", {"taint_flows": [
        {"flow_id": "f1", "sink_call_site_id": "sk1",
         "propagation_steps": [
             {"code_location": "p.js:1", "transformation": None},          # 透传
             {"code_location": "p.js:5", "transformation": "concat"}]}]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [{"id": "p.js:fn:1", "file_path": "p.js", "function_name": "fn",
                    "start_line": 1, "end_line": 10, "source_code": "\n".join(f"l{i}" for i in range(1, 11))}],
        "sink_call_sites": [{"id": "sk1", "callee_name": "x", "category": "SQL",
                             "rule_id": "r", "file_path": "p.js", "line": 9, "column": 0}], "source_points": []})
    _write_intermediate(deliverables, "injection_chain_verdicts.json", {"verdicts": [
        {"flow_id": "f1", "sink_call_site_id": "sk1", "verdict": "vulnerable", "reason": "",
         "sanitizer_annotations": [], "confidence": "high", "vuln_class": "injection"}]})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    nodes = view["trees"][0]["branches"][0]["nodes"]
    assert nodes[0]["has_code"] is False
    assert nodes[1]["has_code"] is True


def test_parameter_graph_missing_keeps_branch_without_nodes(deliverables: Path):
    """§6 降级：parameter_graph 缺 → GitNexus 枝保留、无中间节点（verdict 摘要仍在）。"""
    _write_intermediate(deliverables, "injection_chain_verdicts.json", {"verdicts": [
        {"flow_id": "f9->s9", "sink_call_site_id": "s9", "verdict": "safe",
         "reason": "参数化查询", "sanitizer_annotations": [], "confidence": "high", "vuln_class": "injection"}]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [], "sink_call_sites": [{"id": "s9", "callee_name": "execute", "category": "SQL",
                                           "rule_id": "r", "file_path": "db.py", "line": 7, "column": 0}],
        "source_points": []})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    assert view is not None
    tree = view["trees"][0]
    branch = tree["branches"][0]
    assert branch["verdict"] == "safe"
    assert branch["verdict_reason"] == "参数化查询"
    assert branch["nodes"] == []


def test_llm_finding_stands_own_tree_when_no_alignment(deliverables: Path):
    """LLM finding 对不上 GitNexus sink → 自立 track=llm 树（sink 只有位置无 rule_id）。"""
    _write_intermediate(deliverables, "xss_chain_verdicts.json", {"verdicts": [
        {"flow_id": "f2->s2", "sink_call_site_id": "s2", "verdict": "vulnerable",
         "reason": "", "sanitizer_annotations": [], "confidence": "high", "vuln_class": "xss"}]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [], "sink_call_sites": [{"id": "s2", "callee_name": "innerHTML", "category": "XSS",
                                           "rule_id": "r", "file_path": "app/dom.js", "line": 8, "column": 0}],
        "source_points": []})
    _write_intermediate(deliverables, "xss_exploitation_queue.json", {"vulnerabilities": [
        {"ID": "XSS-VULN-07", "vulnerability_type": "xss", "externally_exploitable": True,
         "confidence": "needs_review", "merge_source": "llm-only",
         "dataflow_steps": [
             {"label": "handler", "file": "o.js", "line": 3, "protection": None},
             {"label": "render", "file": "o.js", "line": 9, "protection": None}]}]})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    assert len(view["trees"]) == 2
    llm_tree = next(t for t in view["trees"] if t["tree_id"].startswith("llm:"))
    assert llm_tree["vuln_class"] == "xss"
    assert llm_tree["sink"]["file"] == "o.js"
    assert llm_tree["sink"]["line"] == 9
    assert llm_tree["sink"]["rule_id"] is None
    branch = llm_tree["branches"][0]
    assert branch["track"] == "llm"
    assert branch["source"]["file"] == "o.js" and branch["source"]["line"] == 3
    assert [n["line"] for n in branch["nodes"]] == []  # 2 步：首=source 末=sink，无中间节点
    assert llm_tree["findings"][0]["id"] == "XSS-VULN-07"


def test_second_order_branch_uses_storage_source(deliverables: Path):
    """§3 规则 4：2ND-GN-* 挂 read-side sink 树，source.type="storage"、write 侧入 label。"""
    _write_intermediate(deliverables, "injection_chain_verdicts.json", {"verdicts": [
        {"flow_id": "u1->s1", "sink_call_site_id": "s1", "verdict": "vulnerable",
         "reason": "", "sanitizer_annotations": [], "confidence": "high", "vuln_class": "injection"}]})
    _write_intermediate(deliverables, "parameter_graph.json", {"taint_flows": [
        {"flow_id": "u1->s1", "entry_point_id": "ep1", "source_param": "bio", "source_type": "storage",
         "sink_call_site_id": "s1", "propagation_steps": []}]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [], "sink_call_sites": [{"id": "s1", "callee_name": "execute", "category": "SQL",
                                           "rule_id": "r", "file_path": "app/db.py", "line": 42, "column": 0}],
        "source_points": []})
    _write_intermediate(deliverables, "injection_exploitation_queue.json", {"vulnerabilities": [
        {"ID": "2ND-GN-01", "vulnerability_type": "second_order_injection",
         "externally_exploitable": True, "confidence": "high", "source_track": "gitnexus",
         "flow_id": "u1->s1", "sink_call": "s1", "verdict": "vulnerable",
         "source": "storage read row.bio (r.js:3)",
         "combined_sources": "write:w.py:7 (users.bio) + read:r.js:3",
         "mismatch_reason": "stored data reaches sink without re-validation"}]})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    tree = view["trees"][0]
    assert tree["tree_id"] == "s1"
    assert tree["findings"][0]["id"] == "2ND-GN-01"
    second = next(b for b in tree["branches"] if b["branch_id"] == "2ND-GN-01")
    assert second["track"] == "gitnexus"
    assert second["source"]["type"] == "storage"
    assert "w.py:7" in second["source"]["label"]


def test_layer2_overlap_does_not_merge_different_sink_trees(deliverables: Path):
    """Fix1：LLM 中间节点与 GN 枝重叠但 sink 位不同 → 不挂，LLM 自立树。"""
    _write_intermediate(deliverables, "injection_chain_verdicts.json", {"verdicts": [
        {"flow_id": "f1->s1", "sink_call_site_id": "s1", "verdict": "vulnerable",
         "reason": "", "sanitizer_annotations": [], "confidence": "high", "vuln_class": "injection"}]})
    _write_intermediate(deliverables, "parameter_graph.json", {"taint_flows": [{
        "flow_id": "f1->s1", "entry_point_id": "ep1", "source_param": "q", "source_type": "query",
        "sink_call_site_id": "s1",
        "propagation_steps": [
            {"from_func_id": "ep1", "to_func_id": "ctrl", "code_location": "ctrl.js:10",
             "transformation": "concat", "intermediate_vars": ["v"]}]}]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [], "sink_call_sites": [{"id": "s1", "callee_name": "query", "category": "SQL",
                                           "rule_id": "r", "file_path": "db1.py", "line": 1, "column": 0}],
        "source_points": []})
    # LLM finding：中间节点 ctrl.js:10 与 GN 枝重叠，但终点（sink 位）在 db3.py:9
    _write_intermediate(deliverables, "injection_exploitation_queue.json", {"vulnerabilities": [
        {"ID": "INJ-VULN-09", "vulnerability_type": "injection", "externally_exploitable": True,
         "confidence": "high", "merge_source": "llm-only", "verdict": "vulnerable",
         "dataflow_steps": [
             {"label": "ctrl", "file": "ctrl.js", "line": 10, "protection": None},
             {"label": "query", "file": "db3.py", "line": 9, "protection": None}]}]})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    assert len(view["trees"]) == 2
    gn_tree = next(t for t in view["trees"] if t["tree_id"] == "s1")
    assert gn_tree["sink"]["file"] == "db1.py" and gn_tree["sink"]["line"] == 1
    # sink 不同的树绝不因中间节点共享被合并：GN 树上无 LLM 枝
    assert all(b["track"] == "gitnexus" for b in gn_tree["branches"])
    llm_tree = next(t for t in view["trees"] if t["tree_id"].startswith("llm:"))
    assert llm_tree["sink"]["file"] == "db3.py" and llm_tree["sink"]["line"] == 9


def test_attached_llm_branch_keeps_terminal_node(deliverables: Path):
    """Fix2：挂靠进 GN 树的 LLM 枝保留终点步（nodes 含末节点 file:line）。"""
    _write_intermediate(deliverables, "injection_chain_verdicts.json", {"verdicts": [
        {"flow_id": "u1->s1", "sink_call_site_id": "s1", "verdict": "vulnerable",
         "reason": "", "sanitizer_annotations": [], "confidence": "high", "vuln_class": "injection"}]})
    _write_intermediate(deliverables, "parameter_graph.json", {"taint_flows": [{
        "flow_id": "u1->s1", "entry_point_id": "ep1", "source_param": "name",
        "source_type": "query", "sink_call_site_id": "s1",
        "propagation_steps": [
            {"from_func_id": "ep1", "to_func_id": "ctrl", "code_location": "c.js:25",
             "transformation": "concat", "intermediate_vars": ["q"]}]}]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [], "sink_call_sites": [{"id": "s1", "callee_name": "execute", "category": "SQL",
                                           "rule_id": "r", "file_path": "app/db.py", "line": 42, "column": 0}],
        "source_points": []})
    # LLM 终点位 c.js:25 落在 GN 枝中间节点上（layer 2）→ 挂靠；3 步：首=source
    _write_intermediate(deliverables, "injection_exploitation_queue.json", {"vulnerabilities": [
        {"ID": "INJ-VULN-11", "vulnerability_type": "injection", "externally_exploitable": True,
         "confidence": "high", "merge_source": "llm-only", "verdict": "vulnerable",
         "dataflow_steps": [
             {"label": "handler", "file": "c.js", "line": 20, "protection": None},
             {"label": "mid", "file": "c.js", "line": 22, "protection": None},
             {"label": "execute", "file": "c.js", "line": 25, "protection": None}]}]})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    assert len(view["trees"]) == 1
    llm_branch = next(b for b in view["trees"][0]["branches"] if b["track"] == "llm")
    # source 单列 + 中间节点 + 终点节点都在：终点（c.js:25）必须可见
    assert llm_branch["source"]["line"] == 20
    assert [n["line"] for n in llm_branch["nodes"]] == [22, 25]
    assert llm_branch["nodes"][-1]["file"] == "c.js"
    assert llm_branch["nodes"][-1]["line"] == 25


def test_llm_branch_verdict_from_finding(deliverables: Path):
    """Fix3：LLM 枝 verdict = finding.verdict；无 verdict 字段才 unknown。"""
    _write_intermediate(deliverables, "xss_exploitation_queue.json", {"vulnerabilities": [
        {"ID": "XSS-VULN-01", "vulnerability_type": "xss", "externally_exploitable": True,
         "confidence": "high", "merge_source": "llm-only", "verdict": "vulnerable",
         "dataflow_steps": [{"label": "sink", "file": "a.js", "line": 4, "protection": None}]},
        {"ID": "XSS-VULN-02", "vulnerability_type": "xss", "externally_exploitable": True,
         "confidence": "needs_review", "merge_source": "llm-only",
         "dataflow_steps": [{"label": "sink", "file": "b.js", "line": 8, "protection": None}]}]})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    by_id = {t["branches"][0]["branch_id"]: t["branches"][0] for t in view["trees"]}
    assert by_id["XSS-VULN-01"]["verdict"] == "vulnerable"   # finding 自带 verdict
    assert by_id["XSS-VULN-02"]["verdict"] == "unknown"       # 缺 verdict 才 unknown


def test_llm_narrative_labels_normalized_to_short_identifiers(deliverables: Path):
    """LLM 叙事句 label 归一（2026-08-27 展示诊断修复）：

    真实产物 dataflow_steps[].label 常是 40-70 字中文叙事句（prompt 契约要
    短标识符，GLM 实际不守）——组装层归一：func/label=句首短标识符（前置
    ≤3 个中文引导字如「传入/调用/读取」采信），句首非函数名 → basename:line
    兜底；原句全句进 note（前端 tooltip/明细行消费）。
    """
    _write_intermediate(deliverables, "xss_exploitation_queue.json", {"vulnerabilities": [
        {"ID": "XSS-VULN-01", "vulnerability_type": "xss", "externally_exploitable": True,
         "confidence": "high", "merge_source": "llm-only", "verdict": "vulnerable",
         "dataflow_steps": [
             {"label": "调用 crmApi.getAuditTip 取服务器审核提示", "file": "a.ts", "line": 10,
              "protection": None},
             {"label": "processAuditTips 将服务器 value 拼接进含 <strong> 的 HTML 模板或原样透传",
              "file": "use-task-detail.ts", "line": 59, "protection": None},
             {"label": "拼接进含 <span style> 的 HTML 字符串并经 t() 纯文本替换",
              "file": "client/Verifying.vue", "line": 24, "protection": None}]}]})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    tree = next(t for t in view["trees"] if t["tree_id"].startswith("llm:"))
    branch = tree["branches"][0]
    # source：句首标识符（「调用」=2 个引导字 ≤3 采信），原句进 note
    assert branch["source"]["label"] == "crmApi.getAuditTip"
    assert branch["source"]["note"] == "调用 crmApi.getAuditTip 取服务器审核提示"
    # 中间节点：句首直接是标识符 → func=标识符，note=原句
    n1 = branch["nodes"][0]
    assert n1["func"] == "processAuditTips"
    assert n1["note"] == ("processAuditTips 将服务器 value 拼接进含 <strong> 的 HTML 模板"
                          "或原样透传")
    # 末步（自立树 sink）：句首非函数名（前置 4 个中文 + 标记符号）→ basename:line 兜底
    assert tree["sink"]["label"] == "Verifying.vue:24"
    assert tree["sink"]["note"] == "拼接进含 <span style> 的 HTML 字符串并经 t() 纯文本替换"


def test_llm_plain_identifier_label_kept_as_is(deliverables: Path):
    """纯标识符 label（无空格无中文）原样进 func/label，note=None（省体积）。"""
    _write_intermediate(deliverables, "xss_exploitation_queue.json", {"vulnerabilities": [
        {"ID": "XSS-VULN-02", "vulnerability_type": "xss", "externally_exploitable": True,
         "confidence": "high", "merge_source": "llm-only", "verdict": "vulnerable",
         "dataflow_steps": [
             {"label": "handler", "file": "o.js", "line": 3, "protection": None},
             {"label": "cursor.execute", "file": "o.js", "line": 9, "protection": None}]}]})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    tree = next(t for t in view["trees"] if t["tree_id"].startswith("llm:"))
    branch = tree["branches"][0]
    assert branch["source"]["label"] == "handler"
    assert branch["source"]["note"] is None
    assert tree["sink"]["label"] == "cursor.execute"
    assert tree["sink"]["note"] is None


def test_llm_short_guiding_prefix_identifier_extracted(deliverables: Path):
    """「动词 + 函数名」形态（前置 ≤3 个中文引导字）：第二个 token 的标识符被采信。"""
    _write_intermediate(deliverables, "injection_exploitation_queue.json", {"vulnerabilities": [
        {"ID": "INJ-VULN-21", "vulnerability_type": "injection", "externally_exploitable": True,
         "confidence": "high", "merge_source": "llm-only", "verdict": "vulnerable",
         "dataflow_steps": [
             {"label": "读取 req.query.threshold", "file": "a.js", "line": 8, "protection": None},
             {"label": "传入 getByUserIdAndThreshold", "file": "b.js", "line": 21, "protection": None},
             {"label": "findOne({userName}) 过滤器注入", "file": "c.js", "line": 91, "protection": None}]}]})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    tree = next(t for t in view["trees"] if t["tree_id"].startswith("llm:"))
    branch = tree["branches"][0]
    assert branch["source"]["label"] == "req.query.threshold"
    assert branch["nodes"][0]["func"] == "getByUserIdAndThreshold"
    assert tree["sink"]["label"] == "findOne"


def test_chain_verdict_duplicate_rows_merged_into_single_branch(deliverables: Path):
    """同 (flow_id, sink) 多行判定合并为一枝（2026-08-27 重复枝修复）。

    真实数据（NodeGoat）chain_verdicts 对同一 flow 最多 x5 行——builder 对
    同 flow 产多条候选链各自判定是上游常态。旧 dedup 键 (flow_id, verdict,
    reason) 挡不住 verdict 冲突行 → 同一数据流在树上画 3-5 条重复枝。
    合并口径（保守原则，与双轨 verdict OR 同精神）：任一 vulnerable →
    vulnerable（宁报不漏）；reason 取选定 verdict 的首条非空。
    """
    _write_intermediate(deliverables, "injection_chain_verdicts.json", {"verdicts": [
        # flow-a 同 sink 3 行全 vulnerable、reason 各异 → 1 枝 reason=首条非空
        {"flow_id": "fa->s1", "sink_call_site_id": "s1", "verdict": "vulnerable",
         "reason": "req.body.preTax reaches eval with no sanitization", "sanitizer_annotations": []},
        {"flow_id": "fa->s1", "sink_call_site_id": "s1", "verdict": "vulnerable",
         "reason": "Sink arg expression is eval(req.body.preTax)", "sanitizer_annotations": []},
        {"flow_id": "fa->s1", "sink_call_site_id": "s1", "verdict": "vulnerable",
         "reason": "", "sanitizer_annotations": []},
    ]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [], "sink_call_sites": [{"id": "s1", "callee_name": "execute", "category": "SQL",
                                           "rule_id": "r", "file_path": "db.py", "line": 5, "column": 0}],
        "source_points": []})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    tree = next(t for t in view["trees"] if t["tree_id"] == "s1")
    flow_a = [b for b in tree["branches"] if b["branch_id"] == "fa->s1"]
    assert len(flow_a) == 1
    assert flow_a[0]["verdict"] == "vulnerable"
    assert flow_a[0]["verdict_reason"] == "req.body.preTax reaches eval with no sanitization"


def test_chain_verdict_conflicting_rows_vulnerable_wins(deliverables: Path):
    """同 flow verdict 冲突（vulnerable × safe）→ 合并一枝取 vulnerable（宁报不漏）。"""
    _write_intermediate(deliverables, "xss_chain_verdicts.json", {"verdicts": [
        {"flow_id": "fx->s2", "sink_call_site_id": "s2", "verdict": "vulnerable",
         "reason": "userName unescaped into value attribute", "sanitizer_annotations": []},
        {"flow_id": "fx->s2", "sink_call_site_id": "s2", "verdict": "safe",
         "reason": "autoescape on", "sanitizer_annotations": []},
        # safe 打头、vulnerable 在后的顺序也必须翻转为 vulnerable（顺序无关）
        {"flow_id": "fy->s2", "sink_call_site_id": "s2", "verdict": "safe",
         "reason": "hardcoded empty string", "sanitizer_annotations": []},
        {"flow_id": "fy->s2", "sink_call_site_id": "s2", "verdict": "vulnerable",
         "reason": "render 时未转义", "sanitizer_annotations": []},
    ]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [], "sink_call_sites": [{"id": "s2", "callee_name": "innerHTML", "category": "XSS",
                                           "rule_id": "r", "file_path": "d.js", "line": 8, "column": 0}],
        "source_points": []})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    tree = next(t for t in view["trees"] if t["tree_id"] == "s2")
    by_flow = {b["branch_id"]: b for b in tree["branches"]}
    assert by_flow["fx->s2"]["verdict"] == "vulnerable"
    assert by_flow["fx->s2"]["verdict_reason"] == "userName unescaped into value attribute"
    assert by_flow["fy->s2"]["verdict"] == "vulnerable"   # safe 打头也翻转
    assert by_flow["fy->s2"]["verdict_reason"] == "render 时未转义"


def test_chain_verdict_distinct_flows_and_sinks_not_merged(deliverables: Path):
    """不同 flow / 不同 sink 各自保留（合并只在同 (flow, sink) 内）。"""
    _write_intermediate(deliverables, "injection_chain_verdicts.json", {"verdicts": [
        {"flow_id": "f1->s1", "sink_call_site_id": "s1", "verdict": "safe",
         "reason": "", "sanitizer_annotations": []},
        {"flow_id": "f2->s1", "sink_call_site_id": "s1", "verdict": "vulnerable",
         "reason": "", "sanitizer_annotations": []},
        # 同 flow 不同 sink（row 自带 sink）→ 两条数据流，不合并
        {"flow_id": "f3", "sink_call_site_id": "s1", "verdict": "safe",
         "reason": "", "sanitizer_annotations": []},
        {"flow_id": "f3", "sink_call_site_id": "s9", "verdict": "vulnerable",
         "reason": "", "sanitizer_annotations": []},
    ]})
    _write_intermediate(deliverables, "parameter_graph.json", {"taint_flows": []})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [],
        "sink_call_sites": [
            {"id": "s1", "callee_name": "execute", "category": "SQL", "rule_id": "r",
             "file_path": "a.py", "line": 1, "column": 0},
            {"id": "s9", "callee_name": "eval", "category": "JS", "rule_id": "r",
             "file_path": "b.py", "line": 9, "column": 0}],
        "source_points": []})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    s1 = next(t for t in view["trees"] if t["tree_id"] == "s1")
    assert {b["branch_id"] for b in s1["branches"]} == {"f1->s1", "f2->s1", "f3"}
    s9 = next(t for t in view["trees"] if t["tree_id"] == "s9")
    assert [b["branch_id"] for b in s9["branches"]] == ["f3"]
    assert s9["branches"][0]["verdict"] == "vulnerable"


def test_chain_verdict_rows_missing_sink_id_merged_via_flow_lookup(deliverables: Path):
    """row 缺 sink_call_site_id → effective sink 从 flow 兜底；同 flow 两行仍合并一枝。"""
    _write_intermediate(deliverables, "injection_chain_verdicts.json", {"verdicts": [
        {"flow_id": "fz", "verdict": "safe", "reason": "参数化查询", "sanitizer_annotations": []},
        {"flow_id": "fz", "verdict": "vulnerable", "reason": "拼接", "sanitizer_annotations": []},
    ]})
    _write_intermediate(deliverables, "parameter_graph.json", {"taint_flows": [
        {"flow_id": "fz", "sink_call_site_id": "s5", "source_param": "q", "source_type": "query",
         "propagation_steps": []}]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [], "sink_call_sites": [{"id": "s5", "callee_name": "exec", "category": "SQL",
                                           "rule_id": "r", "file_path": "e.py", "line": 4, "column": 0}],
        "source_points": []})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    tree = next(t for t in view["trees"] if t["tree_id"] == "s5")
    assert len(tree["branches"]) == 1
    assert tree["branches"][0]["verdict"] == "vulnerable"
    assert tree["branches"][0]["verdict_reason"] == "拼接"


def test_safe_vectors_matched_branch_and_top_level(deliverables: Path):
    """LLM safe 枝来自 safe_vectors：匹配 sink 树挂单节点 safe 枝；匹配不上进顶层区。"""
    _write_intermediate(deliverables, "injection_chain_verdicts.json", {"verdicts": [
        {"flow_id": "f->s1", "sink_call_site_id": "s1", "verdict": "vulnerable",
         "reason": "", "sanitizer_annotations": [], "confidence": "high", "vuln_class": "injection"}]})
    _write_intermediate(deliverables, "code_index.json", {
        "blocks": [], "sink_call_sites": [{"id": "s1", "callee_name": "execute", "category": "SQL",
                                           "rule_id": "r", "file_path": "app/db.py", "line": 42, "column": 0}],
        "source_points": []})
    _write_intermediate(deliverables, "injection_safe_vectors.json", {"vectors": [
        {"subject": "req.query.id", "location": "app/db.py:42", "defense_mechanism": "parseInt"},
        {"subject": "req.body.x", "location": "z.js:1", "defense_mechanism": "escape"},
    ]})
    from supernova_core.services.dataflow_view import assemble_dataflow_view
    view = assemble_dataflow_view(deliverables)
    tree = view["trees"][0]
    safe_branch = next(b for b in tree["branches"] if b["verdict"] == "safe")
    assert safe_branch["track"] == "llm"
    assert safe_branch["source"]["label"] == "req.query.id"
    assert safe_branch["sanitizers"][0]["name"] == "parseInt"
    assert safe_branch["sanitizers"][0]["effective"] is True
    # 未匹配向量 → 顶层 safe_vectors 区
    assert [v["subject"] for v in view["safe_vectors"]] == ["req.body.x"]
    assert view["summary"]["total_sinks"] == 1
