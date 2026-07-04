import json

from fastapi.testclient import TestClient


def _ws(root, name, **kw):
    ws = root / name
    ws.mkdir(parents=True)
    data = {"status": "completed", "scan_type": "whitebox",
            "created_at": "2026-07-02T10:00:00Z", "completed_at": "2026-07-02T10:05:00Z"}
    data.update(kw)
    (ws / "session.json").write_text(json.dumps(data))


def test_list_and_get(app_with_ws, tmp_workspaces):
    _ws(tmp_workspaces, "A")
    client = TestClient(app_with_ws)
    r = client.get("/api/workspaces")
    assert r.status_code == 200
    assert any(w["name"] == "A" for w in r.json())
    assert client.get("/api/workspaces/A").status_code == 200
    assert client.get("/api/workspaces/nope").status_code == 404


def test_report_deliverables_logs(app_with_ws, tmp_workspaces):
    _ws(tmp_workspaces, "A")
    ws = tmp_workspaces / "A"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "comprehensive_security_assessment_report.md").write_text("# R")
    (dl / "xss_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": [{}]}))
    client = TestClient(app_with_ws)
    assert client.get("/api/workspaces/A/report").text == "# R"
    s = client.get("/api/workspaces/A/deliverables").json()
    assert "xss" in s["vuln_queues"]
    f = client.get("/api/workspaces/A/deliverables/xss_exploitation_queue.json")
    assert f.status_code == 200 and "vulnerabilities" in f.json()
    assert client.get("/api/workspaces/A/deliverables/missing.json").status_code == 404


def test_report_no_report_returns_empty_200(app_with_ws, tmp_workspaces):
    """workspace 存在但无报告产物 → 200 + 空文本(前端 ReportTab Empty「报告尚未生成」契约),
    非 404。404 保留给 workspace 不存在(_workspace_path 已抛)。
    回归:曾因后端对无报告返 404,前端把 404 当加载错误显示「报告加载失败:ApiError: API 404」。"""
    _ws(tmp_workspaces, "NoReport")  # 有 session.json,无 deliverables
    client = TestClient(app_with_ws)
    r = client.get("/api/workspaces/NoReport/report")
    assert r.status_code == 200
    assert r.text == ""
    # workspace 不存在仍 404
    assert client.get("/api/workspaces/nope/report").status_code == 404
