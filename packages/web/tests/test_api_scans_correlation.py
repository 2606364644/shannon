"""C5: correlation 详情 API（GET /api/workspaces/{ws}/scans/{scan_id}/correlation）。

组装 deliverables 四产物（topology/boundaries/flows/{vc}_exploitation_queue.json）+
correlation-report.md + session corr_children；404（scan 不存在）/422（非
correlation scan）/200+空产物（关联未跑完，前端显示进行中）语义。
产物 shape 对齐 run_correlation_phase 落盘（deliverables/ 根，无 track 桶）。
"""
import json


def _make_scan(tmp_workspaces, ws, scan_id, scan_type="correlation", status="completed",
               **extra):
    """直接在 tmp_workspaces 建 scan（不经 scan_manager.start，免 temporal）。"""
    scan_dir = tmp_workspaces / ws / "scans" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    sess = {"status": status, "scan_type": scan_type, "created_at": 1780000000.0,
            "web_url": "", "repo_path": "/code", "owner": "web"}
    sess.update(extra)
    (scan_dir / "session.json").write_text(json.dumps(sess))
    return scan_dir


def test_correlation_endpoint_assembles(authed_client, tmp_workspaces):
    """完整组装：四产物原文 + corr_children 透传 + drift_warnings 保守 []。"""
    _make_scan(tmp_workspaces, "WS", scan_id="c1", corr_children=[
        {"service": "gateway", "scan_id": "gw-1", "reused": False},
        {"service": "orders", "scan_id": "ord-1", "reused": True},
    ])
    dlv = tmp_workspaces / "WS" / "scans" / "c1" / "deliverables"
    dlv.mkdir(parents=True)
    (dlv / "cross-service-topology.json").write_text(json.dumps({
        "services": [{"name": "gateway", "role": "frontend", "repo": "/code/gateway"}],
        "edges": [{"from": "gateway", "to": "orders", "protocol": "grpc",
                   "calls": [], "status": "ok", "error": None}]}))
    (dlv / "trust-boundaries.json").write_text(json.dumps([
        {"service": "orders", "method": "CreateOrder", "exposure": "internal",
         "reachable_from": ["gateway"], "reason": "svc 无 authz", "confidence": "high"}]))
    (dlv / "cross-service-flows.json").write_text(json.dumps({
        "flows": [
            {"edge_from": "gateway", "edge_to": "orders", "entry": "POST /api/orders",
             "method": "CreateOrder",
             "call_site": {"file": "handler.go", "line": 42, "snippet": "c.CreateOrder"},
             "vuln_refs": [{"vuln_id": "INJ-01", "service": "orders", "title": "SQLi",
                            "severity": "high", "location": "db.go:10",
                            "source": "queue"}],
             "confidence": "high", "evidence": "e"}],
        "multi_hop_chains": [
            {"path": ["gateway", "orders", "payment"], "basis": "edge-adjacency",
             "confidence": "structural"}]}))
    (dlv / "adjudication-log.json").write_text(json.dumps({"cards": [
        {"direction": "upgrade",
         "finding_ref": {"service": "orders", "vuln_id": "INJ-09", "origin": "dismissed"},
         "conclusion": "vulnerable", "cross_service_context": "via gateway",
         "analysis_process": ["s1"], "verification_evidence": [],
         "reasoning": "reachable now", "confidence": "high"},
        {"direction": "error",
         "finding_ref": {"service": "orders", "vuln_id": "INJ-10", "origin": "queue"},
         "conclusion": "needs-review", "cross_service_context": "",
         "analysis_process": [], "verification_evidence": [],
         "reasoning": "adjudication batch failed: llm down", "confidence": "low"}]}))
    (dlv / "injection_exploitation_queue.json").write_text(json.dumps(
        {"vulnerabilities": [{"ID": "INJ-01", "title": "SQLi", "service": "orders"}]}))
    (dlv / "correlation-report.md").write_text(
        "# Cross-Repo Correlation Report\n\n## 服务拓扑\n")
    r = authed_client.get("/api/workspaces/WS/scans/c1/correlation")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["topology"]["services"][0]["name"] == "gateway"
    assert d["topology"]["edges"][0]["from"] == "gateway"
    assert d["boundaries"][0]["service"] == "orders"
    assert d["flows"][0]["method"] == "CreateOrder"
    assert d["flows"][0]["vuln_refs"][0]["service"] == "orders"
    assert d["multi_hop_chains"][0]["path"] == ["gateway", "orders", "payment"]
    # spec 2026-08-27 §9:adjudication 透传(裁决卡,error 占位卡同样透传)
    assert d["adjudication"]["cards"][0]["direction"] == "upgrade"
    assert d["adjudication"]["cards"][1]["direction"] == "error"
    assert d["merged_vulns"]["injection"][0]["service"] == "orders"
    assert "# Cross-Repo" in d["report_md"]
    assert d["corr_children"] == [
        {"service": "gateway", "scan_id": "gw-1", "reused": False},
        {"service": "orders", "scan_id": "ord-1", "reused": True}]
    assert d["drift_warnings"] == []


def test_correlation_endpoint_legacy_list_flows_compat(authed_client, tmp_workspaces):
    """旧产物兼容：历史 scan 的 flows json 是 list 形态(2026-08-27 前)——
    flows 透传、multi_hop_chains=[]、adjudication None。"""
    _make_scan(tmp_workspaces, "WS", scan_id="c1")
    dlv = tmp_workspaces / "WS" / "scans" / "c1" / "deliverables"
    dlv.mkdir(parents=True)
    (dlv / "cross-service-flows.json").write_text(json.dumps([
        {"edge_from": "g", "edge_to": "o", "entry": "POST /x", "method": "M",
         "call_site": {"file": "f", "line": 1, "snippet": "s"},
         "vuln_refs": [], "confidence": "low", "evidence": "e"}]))
    r = authed_client.get("/api/workspaces/WS/scans/c1/correlation")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["flows"][0]["method"] == "M"
    assert d["multi_hop_chains"] == []
    assert d["adjudication"] is None


def test_correlation_endpoint_adjudication_error_form(authed_client, tmp_workspaces):
    """阶段 B 整体异常留档形态 {"error": ...} 原样透传(spec §10)。"""
    _make_scan(tmp_workspaces, "WS", scan_id="c1")
    dlv = tmp_workspaces / "WS" / "scans" / "c1" / "deliverables"
    dlv.mkdir(parents=True)
    (dlv / "adjudication-log.json").write_text(json.dumps(
        {"error": "adjudication infra down"}))
    r = authed_client.get("/api/workspaces/WS/scans/c1/correlation")
    assert r.status_code == 200, r.text
    assert r.json()["adjudication"] == {"error": "adjudication infra down"}


def test_correlation_endpoint_missing_queue_key_absent(authed_client, tmp_workspaces):
    """缺 {vc}_exploitation_queue.json -> merged_vulns 键缺席（非空数组冒充）。"""
    _make_scan(tmp_workspaces, "WS", scan_id="c2")
    dlv = tmp_workspaces / "WS" / "scans" / "c2" / "deliverables"
    dlv.mkdir(parents=True)
    (dlv / "xss_exploitation_queue.json").write_text(json.dumps(
        {"vulnerabilities": [{"ID": "XSS-01", "service": "gateway"}]}))
    d = authed_client.get("/api/workspaces/WS/scans/c2/correlation").json()
    assert set(d["merged_vulns"]) == {"xss"}


def test_correlation_endpoint_pending(authed_client, tmp_workspaces):
    """主行存在但 deliverables 空 -> 200，flows==[]、topology None（关联进行中/未开始）。"""
    _make_scan(tmp_workspaces, "WS", scan_id="c3", status="running")
    r = authed_client.get("/api/workspaces/WS/scans/c3/correlation")
    assert r.status_code == 200
    d = r.json()
    assert d["flows"] == [] and d["topology"] is None
    assert d["boundaries"] == [] and d["report_md"] is None
    assert d["merged_vulns"] == {}
    assert d["corr_children"] == []


def test_correlation_endpoint_wrong_type(authed_client, tmp_workspaces):
    """白盒 scan -> 422 {"detail": "not a correlation scan"}。"""
    _make_scan(tmp_workspaces, "WS", scan_id="w1", scan_type="whitebox")
    r = authed_client.get("/api/workspaces/WS/scans/w1/correlation")
    assert r.status_code == 422
    assert r.json()["detail"] == "not a correlation scan"


def test_correlation_endpoint_unknown_scan_404(authed_client, tmp_workspaces):
    _make_scan(tmp_workspaces, "WS", scan_id="c1")
    assert authed_client.get("/api/workspaces/WS/scans/nope/correlation").status_code == 404
