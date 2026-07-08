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
    (dl / "xss_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": [
        {"ID": "XSS-01", "vulnerability_type": "xss", "externally_exploitable": False}]}))
    client = TestClient(app_with_ws)
    assert client.get("/api/workspaces/A/report").text == "# R"
    s = client.get("/api/workspaces/A/deliverables").json()
    # 新 DeliverablesSummary shape(非旧 vuln_queues/reports)
    assert any(v.get("ID") == "XSS-01" for v in s["aggregated_vulnerabilities"])
    assert any(f["path"] == "whitebox/xss_exploitation_queue.json" for f in s["files"])
    f = client.get("/api/workspaces/A/deliverables/xss_exploitation_queue.json")
    assert f.status_code == 200 and "vulnerabilities" in f.json()
    assert client.get("/api/workspaces/A/deliverables/missing.json").status_code == 404


def test_deliverables_summary_shape(app_with_ws, tmp_workspaces):
    """deliverables 端点返 DeliverablesSummary {track, files, aggregated_vulnerabilities, notes}。
    files 排除 .git/schemas;kind 判定;path 含 track 前缀(#5,#7)。"""
    _ws(tmp_workspaces, "D")
    ws = tmp_workspaces / "D"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "xss_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": [
        {"ID": "XSS-01", "vulnerability_type": "xss", "externally_exploitable": True}]}))
    (dl / "report.md").write_text("# R")
    (dl / "empty_authz_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": []}))
    (dl / ".git").mkdir()
    (dl / ".git" / "config").write_text("x")
    s = TestClient(app_with_ws).get("/api/workspaces/D/deliverables").json()
    assert s["track"] == "whitebox"
    assert "vuln_queues" not in s and "reports" not in s  # 旧 shape 移除
    paths = [f["path"] for f in s["files"]]
    assert not any(".git" in p for p in paths)  # .git 排除
    assert "whitebox/xss_exploitation_queue.json" in paths  # path 含 track 前缀
    assert any(f["kind"] == "md" for f in s["files"])
    assert any(f["kind"] == "empty_json" for f in s["files"])  # 空 queue → empty_json
    assert any(v["ID"] == "XSS-01" for v in s["aggregated_vulnerabilities"])
    assert s["notes"]["injection_has_no_queue"] is True  # 无 injection queue


def test_deliverables_file_preview_dual_mode(app_with_ws, tmp_workspaces):
    """deliverables 端点双模式:无 path→summary JSON;?path=whitebox/xxx→文件内容 text/plain(#6)。
    前端 FilePreview apiGetText('/deliverables?path=...') 打这个端点。"""
    _ws(tmp_workspaces, "F")
    ws = tmp_workspaces / "F"
    dl = ws / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "xss_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": [{"ID": "X"}]}))
    (dl / "report.md").write_text("# R")
    client = TestClient(app_with_ws)
    # 无 path → JSON summary
    assert client.get("/api/workspaces/F/deliverables").headers["content-type"].startswith("application/json")
    # ?path= md → text/plain + 原样内容
    r = client.get("/api/workspaces/F/deliverables?path=whitebox/report.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "# R"
    # ?path= json → text/plain(JSON 序列化,FilePreview <pre> 渲染)
    rj = client.get("/api/workspaces/F/deliverables?path=whitebox/xss_exploitation_queue.json")
    assert rj.status_code == 200
    assert rj.headers["content-type"].startswith("text/plain")
    assert "X" in rj.text
    # ?path= 不存在 → 404
    assert client.get("/api/workspaces/F/deliverables?path=whitebox/nope.json").status_code == 404


def test_logs_dual_mode(app_with_ws, tmp_workspaces):
    """logs 端点双模式:无 file→{files};有 file→{content}。参数 file 对齐前端 LogsTab ?file=(#8,#9)。"""
    _ws(tmp_workspaces, "L")
    ws = tmp_workspaces / "L"
    (ws / "workflow.log").write_text("wf content")
    (ws / "activity_failures.log").write_text("af content")
    agents = ws / "agents"
    agents.mkdir()
    (agents / "1782_recon_attempt-1.log").write_text("agent content")
    client = TestClient(app_with_ws)
    files = client.get("/api/workspaces/L/logs").json()["files"]
    assert "workflow.log" in files
    assert "activity_failures.log" in files
    assert "agents/1782_recon_attempt-1.log" in files
    assert client.get("/api/workspaces/L/logs?file=workflow.log").json()["content"] == "wf content"
    # agents/ 相对路径回传 → read_log 解析
    assert client.get("/api/workspaces/L/logs?file=agents/1782_recon_attempt-1.log").json()["content"] == "agent content"
    assert client.get("/api/workspaces/L/logs?file=nope.log").status_code == 404


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


def test_get_workspace_returns_session_data(app_with_ws, tmp_workspaces):
    """get_workspace 返 SessionData(含 metrics.phases/agents + session 嵌套),非 indexer row。
    OverviewTab 依赖 metrics 渲染阶段瀑布 + agent 账本(#4)。"""
    _ws(tmp_workspaces, "A",
        created_at=1780000000.0, completed_at=1780000005.0,
        repo_path="/repo",
        metrics={
            "total_duration_ms": 1000, "total_cost_usd": 1.5,
            "phases": {"recon": {"duration_ms": 1000, "duration_percentage": 100, "cost_usd": 1.5, "agent_count": 1}},
            "agents": {"recon": {"duration_ms": 1000, "cost_usd": 1.5, "success": True, "attempt_number": 1, "model": "x"}},
        },
        session={"id": "A", "status": "completed", "createdAt": "2026-05-29T10:00:00Z"})
    d = TestClient(app_with_ws).get("/api/workspaces/A").json()
    # SessionData 字段
    assert d["repo_path"] == "/repo"
    assert d["scan_type"] == "whitebox"
    assert d["status"] == "completed"
    assert isinstance(d["created_at"], (int, float))   # unix number(非 ISO str)
    assert isinstance(d["completed_at"], (int, float))
    # metrics 透传(结构对齐前端 SessionMetrics)
    assert "recon" in d["metrics"]["phases"]
    assert "recon" in d["metrics"]["agents"]
    assert d["metrics"]["total_cost_usd"] == 1.5
    # session 嵌套(旧格式,status 矛盾检测用)
    assert d["session"]["id"] == "A"
    # 不再是 indexer row(无 vuln_count/vuln_counts/is_correlation)
    assert "vuln_count" not in d
    assert "is_correlation" not in d


def test_get_workspace_recently_active_not_500(app_with_ws, tmp_workspaces):
    """回归：get_workspace 对 host CLI 起的活 scan（web 看不到 pid，但 workflow.log 近期被写）
    必须返 200 + status=running，不得 500。曾因 _status_of 改签名收 Path 但此端点仍传 str，
    触发 `'str' object has no attribute 'name'`（completed/failed 态在首行 return 不触发，
    故仅 running 态暴露）。
    """
    import os as _os
    import time as _time
    _ws(tmp_workspaces, "HostAlive", status=None,
        created_at=_time.time(), completed_at=None)
    ws = tmp_workspaces / "HostAlive"
    old = _time.time() - 3600
    _os.utime(ws / "session.json", (old, old))      # session.json 远古
    (ws / "workflow.log").write_text("scan running\n")  # fresh → scan 存活
    r = TestClient(app_with_ws).get("/api/workspaces/HostAlive")
    assert r.status_code == 200
    assert r.json()["status"] == "running"


def test_get_workspace_normalizes_legacy_agents(app_with_ws, tmp_workspaces):
    """get_workspace 归一化旧格式 metrics.agents(juice-shop_whitebox-* 实例)。
    回归:旧格式 final_duration_ms/total_cost_usd/status/attempts[] 直接透传时,
    前端 OverviewTab a.cost_usd.toFixed() 崩(Cannot read properties of undefined)。"""
    _ws(tmp_workspaces, "Legacy", metrics={
        "total_duration_ms": 1000, "total_cost_usd": 8.79,
        "phases": {"recon": {"duration_ms": 1000, "duration_percentage": 100.0,
                             "cost_usd": 8.79, "agent_count": 1}},
        "agents": {
            "recon": {
                "status": "success",
                "attempts": [{"attempt_number": 1, "duration_ms": 1000, "cost_usd": 8.79,
                              "success": True, "model": "claude-opus-4-7"}],
                "final_duration_ms": 1000, "total_cost_usd": 8.79,
                "model": "claude-opus-4-7", "checkpoint": "abc",
            },
        },
    })
    d = TestClient(app_with_ws).get("/api/workspaces/Legacy").json()
    a = d["metrics"]["agents"]["recon"]
    # 归一化到新 schema(types.ts SessionMetrics.agents)
    assert a["cost_usd"] == 8.79            # total_cost_usd → cost_usd
    assert a["duration_ms"] == 1000         # final_duration_ms → duration_ms
    assert a["success"] is True             # status == "success"
    assert a["attempt_number"] == 1         # attempts[-1].attempt_number
    assert a["model"] == "claude-opus-4-7"
    # 旧 key 不透出(前端不再读到 final_duration_ms/total_cost_usd/attempts)
    assert "final_duration_ms" not in a
    assert "total_cost_usd" not in a
    assert "attempts" not in a
