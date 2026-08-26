"""T1（spec 2026-08-26-report-generation-agent-design §4）：report_data schema。

ReportData 是三轨统一的报告 SSOT——组装（build_report_data）产 JSON，
md 导出与前端渲染都吃它。schema 骨架测试先行。
"""
import pytest


def test_report_vulnerability_minimal_fields():
    from supernova_core.models.report_data import (
        ReportVulnerability,
    )
    vuln = ReportVulnerability(
        id="XSS-VULN-01", type="xss", vulnerability_type="Stored",
        title="t", severity="high", merge_source="llm-only",
        externally_exploitable=True,
    )
    assert vuln.id == "XSS-VULN-01"
    assert vuln.merge_source == "llm-only"
    # 未提供的结构化字段缺省为 None / 空列表，不炸
    assert vuln.merged_from == []
    assert vuln.endpoints == []
    assert vuln.poc is None
    assert vuln.problem_points == []


def test_report_vulnerability_requires_id_and_type():
    from supernova_core.models.report_data import (
        ReportVulnerability,
    )
    with pytest.raises(Exception):
        ReportVulnerability(
            vulnerability_type="Stored", title="t", severity="high",
        )
    with pytest.raises(Exception):
        ReportVulnerability(id="XSS-VULN-01", title="t", severity="high")


def test_report_data_top_level_shape():
    from supernova_core.models.report_data import ReportData, ScanMeta
    rd = ReportData(
        scan=ScanMeta(id="NodeGoat-1", track="whitebox"),
        vulnerabilities=[],
    )
    assert rd.schema_version == 1
    assert rd.executive_summary is None  # ④ agent 产物，组装时可缺省
    assert rd.qa is None  # ⑤ agent 产物
    assert rd.vulnerabilities == []
    assert rd.quick_reference == []  # 速查表行（spec 单源化 §5）缺省空


def test_quick_reference_row_model():
    """QuickReferenceRow（spec 2026-08-26-report-single-source-rendering §5）：
    速查表 schema——builder 确定性产，前端与 md 只渲染不派生。"""
    from supernova_core.models.report_data import QuickReferenceRow
    row = QuickReferenceRow(
        id="XSS-VULN-01", title="存储型 XSS：POST /memos",
        params=["memo (body)"],
        endpoints=["POST /memos (write, isLoggedIn)"],
        severity="high", verification="静态分析", confidence="待复核")
    assert row.id == "XSS-VULN-01"
    assert row.params == ["memo (body)"]
    # 最小行：仅 id 必填（title 等缺省容忍——渲染层跳空段）
    minimal = QuickReferenceRow(id="AUTH-VULN-01")
    assert minimal.title is None
    assert minimal.params == []


# ---------- T1 组装器（report_data_builder）----------

async def _write_queue(deliverables, name, vulnerabilities):
    import json
    deliverables.mkdir(parents=True, exist_ok=True)
    (deliverables / name).write_text(
        json.dumps({"vulnerabilities": vulnerabilities}, ensure_ascii=False),
        encoding="utf-8")


async def test_build_report_data_maps_queue_fields(tmp_path):
    from supernova_core.services.report_data_builder import build_report_data
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    await _write_queue(d, "xss_exploitation_queue.json", [{
        "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
        "externally_exploitable": True, "confidence": "needs_review",
        "merge_source": "llm-only",
        "title": "存储型 XSS：POST /memos",
        "severity": "high", "cwe_id": "CWE-79",
        "notes": "路由为 isLoggedIn", "impact": "窃取会话", "remediation": "DOMPurify",
        "endpoints": ["POST /memos (write, isLoggedIn)", "GET /memos (trigger)"],
        "affected_parameters": ["memo (body)"],
        "affected_entries": [{"parameter": "memo", "sink_location": "memos.js:11",
                              "chain_id": "", "track": "llm"}],
        "dataflow_steps": [{"label": "接收", "file": "app/routes/memos.js", "line": 11}],
        "witness_payload": "<img src=x onerror=alert(1)>",
        "verdict": "vulnerable", "verification": "static_analysis",
        "authentication_required": "true",
    }])

    rd = await build_report_data(d, ScanMeta(id="s1", track="whitebox"))

    assert len(rd.vulnerabilities) == 1
    v = rd.vulnerabilities[0]
    assert v.id == "XSS-VULN-01"
    assert v.type == "xss"
    assert v.severity == "high"
    assert v.narrative is not None
    assert v.narrative.cause == "路由为 isLoggedIn"
    assert v.narrative.impact == "窃取会话"
    assert v.narrative.remediation == "DOMPurify"
    # endpoints 串 → 结构化
    assert [e.path for e in v.endpoints] == ["/memos", "/memos"]
    assert v.endpoints[0].method == "POST"
    assert v.endpoints[0].role == "write"
    assert v.endpoints[0].auth == "isLoggedIn"
    assert v.endpoints[1].method == "GET"
    assert v.endpoints[1].role == "trigger"
    # poc/evidence
    assert v.poc is not None and v.poc.witness_payload == "<img src=x onerror=alert(1)>"
    assert v.evidence is not None
    assert v.evidence.verification == "static"
    assert v.evidence.verdict == "vulnerable"
    # raw 保留原始 entry（md 导出复用 render_vuln_card）
    assert v.raw is not None and v.raw["ID"] == "XSS-VULN-01"


async def test_build_report_data_problem_points_passthrough(tmp_path):
    """report_problem_points（问题点富化写回）→ problem_points 透传；畸形条目丢弃。"""
    from supernova_core.services.report_data_builder import build_report_data
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    await _write_queue(d, "xss_exploitation_queue.json", [
        {
            "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
            "severity": "high", "confidence": "high",
            "externally_exploitable": True,
            "report_problem_points": [
                {"location": "app/views/memos.html:31",
                 "description": "memo 未经消毒进入模板渲染",
                 "snippet": "<div><%- memo %></div>"},
                {"description": "缺 location 的畸形条目"},
                {"location": "   "},
            ],
        },
        {
            "ID": "XSS-VULN-02", "vulnerability_type": "Reflected",
            "severity": "high", "confidence": "high",
            "externally_exploitable": True,
        },
    ])
    rd = await build_report_data(d, ScanMeta(id="s1", track="whitebox"))
    by_id = {v.id: v for v in rd.vulnerabilities}
    pp = by_id["XSS-VULN-01"].problem_points
    assert len(pp) == 1
    assert pp[0].location == "app/views/memos.html:31"
    assert pp[0].description == "memo 未经消毒进入模板渲染"
    assert pp[0].snippet == "<div><%- memo %></div>"
    # 无写回字段的卡缺省空列表（GN/黑盒卡降级路径）
    assert by_id["XSS-VULN-02"].problem_points == []


async def test_build_report_data_endpoint_fallback(tmp_path):
    """无 endpoints 列表时从 endpoint/path 兜底（GN 卡路径）。"""
    from supernova_core.services.report_data_builder import build_report_data
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    await _write_queue(d, "injection_exploitation_queue.json", [{
        "ID": "INJ-GN-01", "vulnerability_type": "Code Injection",
        "externally_exploitable": True, "confidence": "low",
        "merge_source": "gitnexus-only",
        "endpoint": None,
        "path": "preTax -> app/routes/contributions.js:ContributionsHandler:eval:32:23 (llm-pass-failed, needs_review)",
        "sink_call": "app/routes/contributions.js:ContributionsHandler:eval:32:23",
    }])
    rd = await build_report_data(d, ScanMeta(id="s1", track="whitebox"))
    v = rd.vulnerabilities[0]
    # path 提取不出 METHOD /route 时 endpoints 允许为空（T3 富化补）
    assert v.type == "injection"
    # affected_entries 无 → 空；sink 信息在 raw 里保留
    assert v.raw is not None and v.raw["sink_call"].startswith("app/routes/contributions.js")


async def test_build_report_data_stats_aggregation(tmp_path):
    from supernova_core.services.report_data_builder import build_report_data
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    await _write_queue(d, "xss_exploitation_queue.json", [
        {"ID": "XSS-VULN-01", "vulnerability_type": "Stored",
         "externally_exploitable": True, "confidence": "high", "severity": "high"},
        {"ID": "XSS-GN-01", "vulnerability_type": "Reflected",
         "externally_exploitable": True, "confidence": "low"},
    ])
    await _write_queue(d, "ssrf_exploitation_queue.json", [
        {"ID": "SSRF-VULN-01", "vulnerability_type": "SSRF",
         "externally_exploitable": False, "confidence": "high", "severity": "critical"},
    ])
    rd = await build_report_data(d, ScanMeta(id="s1", track="whitebox"))
    assert rd.stats is not None
    assert rd.stats.by_type["xss"].count == 2
    assert rd.stats.by_type["ssrf"].count == 1
    assert rd.stats.by_type["xss"].severity_range == "high"
    # severity 缺省走 effective_severity 兜底
    assert rd.stats.by_severity["critical"] == 1
    assert "high" in rd.stats.by_severity


async def test_write_report_data_json(tmp_path):
    from supernova_core.services.report_data_builder import (
        build_report_data, write_report_data,
    )
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    await _write_queue(d, "xss_exploitation_queue.json", [{
        "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
        "externally_exploitable": True, "confidence": "high", "title": "中文标题",
    }])
    rd = await build_report_data(d, ScanMeta(id="s1", track="whitebox"))
    out = d / "report_data.json"
    await write_report_data(rd, out)
    import json
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["scan"]["id"] == "s1"
    assert data["vulnerabilities"][0]["title"] == "中文标题"  # ensure_ascii=False


# ---------- T1 md 导出（report_markdown_exporter）----------
# 已迁出至 test_report_markdown_exporter.py（spec 2026-08-26 单源化，
# builder 与 exporter 测试文件分离）。
