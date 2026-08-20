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
