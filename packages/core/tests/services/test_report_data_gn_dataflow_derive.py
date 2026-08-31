"""GN 卡 dataflow_steps 确定性派生（2026-08-31 用户诉求：报告里 GN 轨漏洞无数据流）。

定位：**零成本兜底层**——report_data_builder 组装时，queue 的 dataflow_steps
为空（LLM 轨专属字段，GN builder 不产；GN-only 深度富化 agent 未回填）且卡为
GN taint 卡时，从 parameter_graph.json 的 taint_flows 派生
``source(param@file:line) → propagation hops(vars/transformation@file:line)
→ sink(callee@file:line)``。GN 自有精确形态（真实行号/变量名），不模仿 LLM
叙事；富化 agent 成功回填的深链在 queue 里已有 → 不覆盖（兜底语义天然成立）。
queue SSOT 不写派生值（dataflow_steps 字段契约「LLM 轨专属」不破）——只落
report_data.json，web 卡 / comprehensive md / 分项 findings.md 单源渲染全吃到。
"""
import json


async def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


_ENTRY = "app/routes/contributions.js:ContributionsHandler:6"
_SINK_ID = "app/routes/contributions.js:ContributionsHandler:render:21:19"


def _gn_finding(**over):
    entry = {
        "ID": "XSS-GN-01", "vulnerability_type": "xss",
        "externally_exploitable": True, "confidence": "medium",
        "source_track": "gitnexus", "merge_source": "gitnexus-only",
        "title": "xss：roth 未过滤进模板", "verdict": "vulnerable",
        "source": f"roth ({_ENTRY})",
        "sink_call": _SINK_ID,
        "flow_id": "F1",
    }
    entry.update(over)
    return entry


def _pgraph():
    return {"taint_flows": [{
        "flow_id": "F1", "entry_point_id": _ENTRY,
        "source_param": "roth", "source_type": "body",
        "propagation_steps": [
            {"from_func_id": _ENTRY, "from_param": "req",
             "to_func_id": _SINK_ID, "to_param": "x",
             "transformation": None,
             "code_location": "app/routes/contributions.js:32",
             "intermediate_vars": ["req.body.roth", "roth"]},
            {"from_func_id": _ENTRY, "from_param": "req",
             "to_func_id": _SINK_ID, "to_param": "x",
             "transformation": "模板字符串内插",
             "code_location": "app/routes/contributions.js:35",
             "intermediate_vars": []},
        ],
        "sink_call_site_id": _SINK_ID,
    }]}


def _code_index():
    return {
        "source_points": [{
            "entry_point_id": _ENTRY, "param_name": "roth",
            "expression": "req.body.roth",
            "file_path": "app/routes/contributions.js", "line": 31,
        }],
        "sink_call_sites": [{
            "id": _SINK_ID, "callee_name": "render", "callee_receiver": "res",
            "file_path": "app/routes/contributions.js", "line": 21,
        }],
        "entry_points": [], "blocks": [],
    }


async def _build(tmp_path, findings, *, pgraph=True, code_index=True,
                 queue_name="xss_exploitation_queue.json"):
    from supernova_core.models.report_data import ScanMeta
    from supernova_core.services.report_data_builder import build_report_data

    d = tmp_path / "deliverables"
    await _write_json(d / queue_name, {"vulnerabilities": findings})
    if pgraph:
        await _write_json(d / "parameter_graph.json", _pgraph())
    if code_index:
        await _write_json(d / "code_index.json", _code_index())
    rd = await build_report_data(d, ScanMeta(id="s1", track="whitebox"))
    return rd.vulnerabilities[0]


async def test_gn_finding_flow_steps_derived(tmp_path):
    """主干：GN 卡空 steps → source + hops + sink 三段派生（GN 精确形态）。
    source label 带入口通道（param (source_type)，对齐 affected_parameters
    "email (body)" 惯例）。"""
    v = await _build(tmp_path, [_gn_finding()])
    steps = v.dataflow_steps
    assert len(steps) == 4
    # source：param + 入口通道 + source_point 锚定的 file:line
    assert steps[0]["label"] == "roth (body)"
    assert steps[0]["file"] == "app/routes/contributions.js"
    assert steps[0]["line"] == 31
    # hop1：无 transformation → intermediate_vars 拼接
    assert "req.body.roth" in steps[1]["label"] and "roth" in steps[1]["label"]
    assert steps[1]["file"] == "app/routes/contributions.js"
    assert steps[1]["line"] == 32
    # hop2：transformation 优先
    assert steps[2]["label"] == "模板字符串内插"
    assert steps[2]["line"] == 35
    # sink：receiver.callee + sink meta file:line
    assert steps[3]["label"] == "res.render"
    assert steps[3]["file"] == "app/routes/contributions.js"
    assert steps[3]["line"] == 21


async def test_gn_derived_steps_not_written_to_raw(tmp_path):
    """queue SSOT 不动：派生值只进 ReportVulnerability.dataflow_steps，
    raw（queue entry dump）不出现 dataflow_steps 键。"""
    v = await _build(tmp_path, [_gn_finding()])
    assert "dataflow_steps" not in (v.raw or {})
    assert len(v.dataflow_steps) == 4


async def test_queue_steps_take_precedence_over_derivation(tmp_path):
    """富化 agent 回填的深链在 queue 里 → 不覆盖（兜底层语义）。"""
    deep = [{"label": "富化深链第 1 步", "file": "a.js", "line": 1}]
    v = await _build(tmp_path, [_gn_finding(dataflow_steps=deep)])
    assert v.dataflow_steps == deep


async def test_llm_finding_not_derived(tmp_path):
    """LLM 卡（无 source_track/flow_id）空 steps 保持空——evidence_chain
    兜底是渲染层的事，builder 不代劳。"""
    v = await _build(tmp_path, [{
        "ID": "XSS-VULN-01", "vulnerability_type": "xss",
        "externally_exploitable": True, "confidence": "high",
        "merge_source": "llm-only", "title": "t", "verdict": "vulnerable",
    }])
    assert v.dataflow_steps == []


async def test_no_parameter_graph_no_derivation(tmp_path):
    """parameter_graph 缺（确定性层失败档）→ 诚实空，不虚构。"""
    v = await _build(tmp_path, [_gn_finding()], pgraph=False)
    assert v.dataflow_steps == []


async def test_no_code_index_still_derives(tmp_path):
    """code_index 缺（source_point/sink meta 不可用）→ 仍派生：source 无
    file:line、sink 从 sink_call_site_id 解析（file:caller:callee:line:col）。"""
    v = await _build(tmp_path, [_gn_finding()], code_index=False)
    steps = v.dataflow_steps
    assert len(steps) == 4
    assert steps[0]["label"] == "roth (body)"
    assert steps[0].get("file") is None
    assert steps[3]["label"] == "render"          # 无 meta → id 解 callee
    assert steps[3]["file"] == "app/routes/contributions.js"
    assert steps[3]["line"] == 21


async def test_sanitize_hint_transformation_maps_to_protection(tmp_path):
    """transformation 的 sanitize_hint: 前缀（GN 净化提示，真实数据里非空
    transformation 主要是它）→ 同步挂 step.protection——web 卡每步有现成
    「防护」渲染位（LLM 轨 steps[].protection 同位）；label 保留全文。"""
    hint = ("sanitize_hint:swig (via consolidate) template engine "
            "autoescapes HTML in default {{...}} output")
    pgraph = _pgraph()
    pgraph["taint_flows"][0]["propagation_steps"][1]["transformation"] = hint
    from supernova_core.models.report_data import ScanMeta
    from supernova_core.services.report_data_builder import build_report_data
    d = tmp_path / "deliverables"
    await _write_json(d / "xss_exploitation_queue.json",
                      {"vulnerabilities": [_gn_finding()]})
    await _write_json(d / "parameter_graph.json", pgraph)
    await _write_json(d / "code_index.json", _code_index())
    rd = await build_report_data(d, ScanMeta(id="s1", track="whitebox"))
    hop = rd.vulnerabilities[0].dataflow_steps[2]
    # label 超长按 _HOP_LABEL_MAX 截断（紧凑展示）；protection 保留全文
    assert hop["label"].startswith("sanitize_hint:swig (via consolidate)")
    assert len(hop["label"]) == 80 and hop["label"].endswith("…")
    assert hop["protection"] == ("swig (via consolidate) template engine "
                                 "autoescapes HTML in default {{...}} output")


async def test_sanitizer_annotation_attaches_protection(tmp_path):
    """finding.sanitizer_annotations 按 code_location 的 file:line 匹配挂
    protection（matched_text 优先）；匹配不到任何步的标注丢弃（不虚构挂点）。"""
    anns = [
        {"matched_text": "DOMPurify.sanitize", "rule_id": "xss-sanitize-dompurify",
         "defense_type": "output_encoding",
         "code_location": "app/routes/contributions.js:32"},
        {"matched_text": "漏挂的", "rule_id": "r",
         "code_location": "app/nowhere.js:1"},
    ]
    v = await _build(tmp_path, [_gn_finding(sanitizer_annotations=anns)])
    steps = v.dataflow_steps
    assert steps[1]["protection"] == "DOMPurify.sanitize"   # :32 的 hop
    assert "protection" not in steps[0]                     # 无匹配不挂


async def test_2nd_gn_storage_source_derived(tmp_path):
    """2ND-GN 存储型（second_order_builder 产 InjectionVulnerability → injection
    队列）：source = storage 写侧（combined_sources 的 write:file:line 并入
    label，对齐 dataflow_view 二阶枝口径）。"""
    finding = _gn_finding(
        ID="2ND-GN-1", source="users.bio", sink_call=None,
        combined_sources="write:app/routes/contributions.js:34 (users.bio)"
                         " + read:app/views/profile.jade:3")
    v = await _build(tmp_path, [finding],
                     queue_name="injection_exploitation_queue.json")
    steps = v.dataflow_steps
    assert len(steps) >= 3
    s0 = steps[0]
    assert "users.bio" in s0["label"] and "write" in s0["label"]
    assert s0["file"] == "app/routes/contributions.js"
    assert s0["line"] == 34


async def test_flow_missing_two_point_fallback(tmp_path):
    """flow 缺（flow_id 不在 pgraph）：source（finding 自述）+ sink（sink_call
    解析）两点直连；连 source 素材都没有 → 空交还渲染层 evidence_chain 兜底。"""
    v = await _build(tmp_path, [_gn_finding(flow_id="NOPE")])
    steps = v.dataflow_steps
    assert len(steps) == 2
    assert steps[0]["label"] == "roth"
    assert steps[1]["label"] == "res.render"

    v2 = await _build(tmp_path, [_gn_finding(flow_id="NOPE", source=None,
                                             sink_call=None,
                                             combined_sources=None)])
    assert v2.dataflow_steps == []


async def test_auth_class_not_derived(tmp_path):
    """auth/authz 非 taint 类（无 flow 语义）不派生。"""
    v = await _build(tmp_path, [{
        "ID": "AUTH-VULN-01", "vulnerability_type": "auth",
        "externally_exploitable": True, "confidence": "high",
        "source_track": "gitnexus", "merge_source": "gitnexus-only",
        "title": "t", "endpoint": "/login",
        "missing_defense": "无速率限制",
    }], queue_name="auth_exploitation_queue.json")
    assert v.dataflow_steps == []
