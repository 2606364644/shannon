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
    (dlv / "cross-service-flows.json").write_text(json.dumps([
        {"edge_from": "gateway", "edge_to": "orders", "entry": "POST /api/orders",
         "method": "CreateOrder",
         "call_site": {"file": "handler.go", "line": 42, "snippet": "c.CreateOrder"},
         "vuln_refs": [{"service": "orders", "title": "SQLi", "severity": "high",
                        "location": "db.go:10"}],
         "confidence": "high", "evidence": "e"}]))
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
    assert d["merged_vulns"]["injection"][0]["service"] == "orders"
    assert "# Cross-Repo" in d["report_md"]
    assert d["corr_children"] == [
        {"service": "gateway", "scan_id": "gw-1", "reused": False},
        {"service": "orders", "scan_id": "ord-1", "reused": True}]
    assert d["drift_warnings"] == []


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
