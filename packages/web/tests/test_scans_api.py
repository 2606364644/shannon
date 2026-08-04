"""T4: scan-scoped API 路由 + shim 测试。

GET /{ws}/scans、/{ws}/scans/{scan_id}(detail/report/deliverables/logs) 按 scan_id；
scan_id 路径校验拒越界 -> 404；resume interrupted/crashed -> 202、completed/failed/running ->
422；shim GET /{ws} 含 scans[]；shim DELETE /api/scan/{ws} cancel latest。
"""
import json

import pytest


def _make_scan(tmp_workspaces, ws, scan_id="20260727-120000", status="completed",
               owner="web", **extra):
    """直接在 tmp_workspaces 建 scan（不经 scan_manager.start，免 temporal）。"""
    scan_dir = tmp_workspaces / ws / "scans" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    sess = {"status": status, "scan_type": "whitebox", "created_at": 1780000000.0,
            "web_url": "http://e", "repo_path": "/code", "owner": owner}
    sess.update(extra)
    (scan_dir / "session.json").write_text(json.dumps(sess))
    return scan_dir


def _csrf(c):
    return c.get("/api/auth/csrf").json()["csrf_token"]


class FakeSM:
    """隔离 scan_manager（免 temporal），记录 resume/cancel/delete 调用。"""
    def __init__(self):
        self.resumed = []
        self.cancelled = []
        self.deleted = []
        self.resume_exc = None
        self.delete_exc = None

    async def resume(self, ws, scan_id):
        if self.resume_exc:
            raise self.resume_exc
        self.resumed.append((ws, scan_id))
        return ws, scan_id

    async def cancel(self, ws, scan_id=None):
        self.cancelled.append((ws, scan_id))
        return {"cancelled": scan_id if scan_id else ws}

    async def delete(self, ws, scan_id):
        if self.delete_exc:
            raise self.delete_exc
        self.deleted.append((ws, scan_id))
        return None if scan_id == "nope" else {"deleted": scan_id}

    def active_pids(self):
        return {}


# ── scan-scoped 读路由 ─────────────────────────────────────────────────────

def test_list_scans(authed_client, tmp_workspaces):
    _make_scan(tmp_workspaces, "WS", scan_id="20260727-120000")
    _make_scan(tmp_workspaces, "WS", scan_id="20260727-130000")
    r = authed_client.get("/api/workspaces/WS/scans")
    assert r.status_code == 200
    ids = {s["scan_id"] for s in r.json()}
    assert ids == {"20260727-120000", "20260727-130000"}


def test_get_scan_detail(authed_client, tmp_workspaces):
    _make_scan(tmp_workspaces, "WS", scan_id="s1", status="completed",
               repo_path="/code/x", source_repo="group/repo-a")
    r = authed_client.get("/api/workspaces/WS/scans/s1")
    assert r.status_code == 200
    d = r.json()
    assert d["repo_path"] == "/code/x"
    assert d["scan_type"] == "whitebox"
    assert d["status"] == "completed"
    # 重跑预填字段：白盒 source_repo；无 scan-config.yaml -> authentication None
    assert d["source_repo"] == "group/repo-a"
    assert d["reuse_whitebox_scan_id"] is None
    assert d["authentication"] is None


def test_get_scan_detail_rerun_preset_blackbox(authed_client, tmp_workspaces):
    """黑盒 _scan_detail 返 reuse_whitebox_scan_id + authentication（读 scan-config.yaml）。"""
    bb_dir = _make_scan(tmp_workspaces, "WS", scan_id="bb1", scan_type="blackbox",
                        reuse_whitebox_scan_id="wb1")
    (bb_dir / "scan-config.yaml").write_text("""authentication:
  login_type: form
  login_url: http://t/login
  credentials:
    username: admin
    password: pw
  success_condition:
    type: url_contains
    value: welcome
""", encoding="utf-8")
    r = authed_client.get("/api/workspaces/WS/scans/bb1")
    assert r.status_code == 200
    d = r.json()
    assert d["reuse_whitebox_scan_id"] == "wb1"
    auth = d["authentication"]
    assert auth["login_type"] == "form"
    assert auth["login_url"] == "http://t/login"
    assert auth["credentials"]["username"] == "admin"


def test_get_scan_detail_no_auth_config(authed_client, tmp_workspaces):
    """黑盒无 scan-config.yaml（未启用登录）-> authentication None，不阻塞详情。"""
    _make_scan(tmp_workspaces, "WS", scan_id="bb2", scan_type="blackbox")
    r = authed_client.get("/api/workspaces/WS/scans/bb2")
    assert r.status_code == 200
    assert r.json()["authentication"] is None


def test_get_scan_404_unknown(authed_client, tmp_workspaces):
    _make_scan(tmp_workspaces, "WS", scan_id="s1")
    assert authed_client.get("/api/workspaces/WS/scans/nope").status_code == 404


# 注：scan_id 路径遍历防护（拒 ..///）在单测 test_scan_store.py::test_get_scan_dir_rejects_traversal
# 覆盖（HTTP 层 Starlette 会规范化 /scans/.. -> /WS/，不作为 scan_id 到达路由）。


def test_scan_deliverables(authed_client, tmp_workspaces):
    scan_dir = _make_scan(tmp_workspaces, "WS", scan_id="s1")
    dl = scan_dir / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "report.md").write_text("# R")
    s = authed_client.get("/api/workspaces/WS/scans/s1/deliverables").json()
    assert any(f["path"] == "whitebox/report.md" for f in s["files"])


def test_scan_report(authed_client, tmp_workspaces):
    scan_dir = _make_scan(tmp_workspaces, "WS", scan_id="s1")
    dl = scan_dir / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "comprehensive_security_assessment_report.md").write_text("# 综合报告")
    assert authed_client.get("/api/workspaces/WS/scans/s1/report").text == "# 综合报告"


def test_scan_report_blackbox_track(authed_client, tmp_workspaces):
    """黑盒扫描报告落在 deliverables/blackbox/。read() 默认 track 必须自动推断到
    blackbox(对齐 summary/read_poc),否则 report_for 按默认 whitebox track 找不到
    -> FileNotFoundError -> 500（regression: repo-20260802-154427 报告页加载失败）。"""
    scan_dir = _make_scan(tmp_workspaces, "WS", scan_id="s1", scan_type="blackbox")
    dl = scan_dir / "deliverables" / "blackbox"
    dl.mkdir(parents=True)
    (dl / "comprehensive_security_assessment_report.md").write_text("# 黑盒综合报告")
    assert authed_client.get("/api/workspaces/WS/scans/s1/report").text == "# 黑盒综合报告"


def test_scan_logs(authed_client, tmp_workspaces):
    scan_dir = _make_scan(tmp_workspaces, "WS", scan_id="s1")
    (scan_dir / "workflow.log").write_text("wf")
    files = authed_client.get("/api/workspaces/WS/scans/s1/logs").json()["files"]
    assert "workflow.log" in files


# ── resume（用户决策：仅 interrupted/crashed 可 resume）──────────────────────

def test_resume_interrupted_202(authed_client, app_with_ws, tmp_workspaces):
    _make_scan(tmp_workspaces, "WS", scan_id="s1", status="interrupted")
    fake = FakeSM()
    app_with_ws.state.scan_manager = fake
    tok = _csrf(authed_client)
    r = authed_client.post("/api/workspaces/WS/scans/s1/resume",
                           headers={"X-CSRF-Token": tok})
    assert r.status_code == 202
    assert fake.resumed == [("WS", "s1")]


def test_resume_crashed_202(authed_client, app_with_ws, tmp_workspaces):
    _make_scan(tmp_workspaces, "WS", scan_id="s1", status="crashed")
    fake = FakeSM()
    app_with_ws.state.scan_manager = fake
    tok = _csrf(authed_client)
    assert authed_client.post("/api/workspaces/WS/scans/s1/resume",
                              headers={"X-CSRF-Token": tok}).status_code == 202


def test_resume_completed_422(authed_client, app_with_ws, tmp_workspaces):
    """completed scan 不可 resume -> 422（用重扫 POST /api/scan 起 scan）。"""
    _make_scan(tmp_workspaces, "WS", scan_id="s1", status="completed")
    fake = FakeSM()
    fake.resume_exc = ValueError("该扫描状态为 completed，不可恢复，请重新扫描")
    app_with_ws.state.scan_manager = fake
    tok = _csrf(authed_client)
    assert authed_client.post("/api/workspaces/WS/scans/s1/resume",
                              headers={"X-CSRF-Token": tok}).status_code == 422


def test_resume_failed_422(authed_client, app_with_ws, tmp_workspaces):
    """failed scan 不可 resume -> 422（扫描失败应重扫，旧记录保留）。"""
    _make_scan(tmp_workspaces, "WS", scan_id="s1", status="failed")
    fake = FakeSM()
    fake.resume_exc = ValueError("该扫描状态为 failed，不可恢复，请重新扫描")
    app_with_ws.state.scan_manager = fake
    tok = _csrf(authed_client)
    assert authed_client.post("/api/workspaces/WS/scans/s1/resume",
                              headers={"X-CSRF-Token": tok}).status_code == 422


def test_resume_unknown_scan_404(authed_client, app_with_ws, tmp_workspaces):
    fake = FakeSM()
    fake.resume_exc = ValueError("scan 不存在")
    app_with_ws.state.scan_manager = fake
    tok = _csrf(authed_client)
    assert authed_client.post("/api/workspaces/WS/scans/nope/resume",
                              headers={"X-CSRF-Token": tok}).status_code == 404


def test_cancel_scan_by_id(authed_client, app_with_ws, tmp_workspaces):
    """POST /scans/{id}/cancel 取消（DELETE 端点已让位给真删）。"""
    _make_scan(tmp_workspaces, "WS", scan_id="s1", status="running")
    fake = FakeSM()
    app_with_ws.state.scan_manager = fake
    tok = _csrf(authed_client)
    r = authed_client.post("/api/workspaces/WS/scans/s1/cancel",
                           headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert fake.cancelled == [("WS", "s1")]


def test_cancel_scan_passes_through_via_signal(authed_client, app_with_ws, tmp_workspaces):
    """scan-scoped cancel 返 via:signal 时 api 透传给前端（ws 列表 cancelActiveScan 依赖）。"""
    _make_scan(tmp_workspaces, "WS", scan_id="s1", status="running")

    class HostSM:
        def __init__(self):
            self.cancelled = []

        async def cancel(self, ws, scan_id):
            self.cancelled.append((ws, scan_id))
            return {"cancelled": scan_id, "via": "signal"}

        def active_pids(self):
            return {}

    fake = HostSM()
    app_with_ws.state.scan_manager = fake
    tok = _csrf(authed_client)
    r = authed_client.post("/api/workspaces/WS/scans/s1/cancel", headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert r.json() == {"cancelled": "s1", "via": "signal"}
    assert fake.cancelled == [("WS", "s1")]


# ── delete（真删，spec §5.1 DELETE）─────────────────────────────────────────

def test_delete_scan_by_id(authed_client, app_with_ws, tmp_workspaces):
    """DELETE /scans/{id} 真删（调 sm.delete），返 {deleted:id}。"""
    _make_scan(tmp_workspaces, "WS", scan_id="s1", status="completed")
    fake = FakeSM()
    app_with_ws.state.scan_manager = fake
    tok = _csrf(authed_client)
    r = authed_client.delete("/api/workspaces/WS/scans/s1", headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert r.json() == {"deleted": "s1"}
    assert fake.deleted == [("WS", "s1")]


def test_delete_running_scan_409(authed_client, app_with_ws, tmp_workspaces):
    """running scan 删除 -> 409（先取消再删，对齐 delete_workspace 删 ws 前 running 检查）。"""
    from supernova_web.components.scan_manager import ScanRunning
    _make_scan(tmp_workspaces, "WS", scan_id="s1", status="running")
    fake = FakeSM()
    fake.delete_exc = ScanRunning("s1")
    app_with_ws.state.scan_manager = fake
    tok = _csrf(authed_client)
    r = authed_client.delete("/api/workspaces/WS/scans/s1", headers={"X-CSRF-Token": tok})
    assert r.status_code == 409


def test_delete_unknown_scan_404(authed_client, app_with_ws, tmp_workspaces):
    """scan 不存在 -> sm.delete 返 None -> 404。"""
    fake = FakeSM()
    app_with_ws.state.scan_manager = fake
    tok = _csrf(authed_client)
    r = authed_client.delete("/api/workspaces/WS/scans/nope", headers={"X-CSRF-Token": tok})
    assert r.status_code == 404


# ── shim 已彻底移除（scan-scoped 是唯一取消路径）──────────────────────────────


def test_legacy_ws_scoped_delete_now_404(authed_client, tmp_workspaces):
    """旧 DELETE /api/scan/{ws} shim 已移除 -> 404（路由不存在；scan-scoped DELETE 才是唯一取消路径）。"""
    _make_scan(tmp_workspaces, "WS", scan_id="s1", status="running")
    tok = _csrf(authed_client)
    assert authed_client.delete("/api/scan/WS", headers={"X-CSRF-Token": tok}).status_code == 404


def _scan_with(tmp_workspaces, ws, scan_id="s1", status="completed", **extra):
    """建 scan + 写 session.json 任意字段（metrics/session/repo_path 等）。"""
    import json as _json
    scan_dir = tmp_workspaces / ws / "scans" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    sess = {"status": status, "scan_type": "whitebox", "created_at": 1780000000.0,
            "web_url": "http://e", "repo_path": "/code", "owner": "web"}
    sess.update(extra)
    (scan_dir / "session.json").write_text(_json.dumps(sess))
    return scan_dir


def test_scan_deliverables_summary_shape(authed_client, tmp_workspaces):
    """deliverables 摘要：track/files(kind)/aggregated_vulns/notes；.git 排除。"""
    scan_dir = _scan_with(tmp_workspaces, "D", scan_id="s1")
    dl = scan_dir / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "xss_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": [
        {"ID": "XSS-01", "vulnerability_type": "xss", "externally_exploitable": True}]}))
    (dl / "report.md").write_text("# R")
    (dl / "empty_authz_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": []}))
    (dl / ".git").mkdir()
    (dl / ".git" / "config").write_text("x")
    s = authed_client.get("/api/workspaces/D/scans/s1/deliverables").json()
    assert s["track"] == "whitebox"
    paths = [f["path"] for f in s["files"]]
    assert not any(".git" in p for p in paths)
    assert "whitebox/xss_exploitation_queue.json" in paths
    assert any(f["kind"] == "md" for f in s["files"])
    assert any(f["kind"] == "empty_json" for f in s["files"])
    assert any(v["ID"] == "XSS-01" for v in s["aggregated_vulnerabilities"])
    assert s["notes"]["injection_has_no_queue"] is True


def test_scan_deliverables_file_dual_mode(authed_client, tmp_workspaces):
    """?path= md -> text/plain 原样；json -> text/plain 序列化；不存在 -> 404。"""
    scan_dir = _scan_with(tmp_workspaces, "F", scan_id="s1")
    dl = scan_dir / "deliverables" / "whitebox"
    dl.mkdir(parents=True)
    (dl / "xss_exploitation_queue.json").write_text(json.dumps({"vulnerabilities": [{"ID": "X"}]}))
    (dl / "report.md").write_text("# R")
    client = authed_client
    r = client.get("/api/workspaces/F/scans/s1/deliverables?path=whitebox/report.md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain") and r.text == "# R"
    rj = client.get("/api/workspaces/F/scans/s1/deliverables?path=whitebox/xss_exploitation_queue.json")
    assert rj.status_code == 200 and "X" in rj.text
    assert client.get("/api/workspaces/F/scans/s1/deliverables?path=whitebox/nope.json").status_code == 404


def test_scan_logs_dual_mode(authed_client, tmp_workspaces):
    """logs 无 file -> {files}；有 file -> {content}；agents/ 相对路径解析。"""
    scan_dir = _scan_with(tmp_workspaces, "L", scan_id="s1")
    (scan_dir / "workflow.log").write_text("wf content")
    agents = scan_dir / "agents"; agents.mkdir()
    (agents / "recon.log").write_text("agent content")
    client = authed_client
    files = client.get("/api/workspaces/L/scans/s1/logs").json()["files"]
    assert "workflow.log" in files and "agents/recon.log" in files
    assert client.get("/api/workspaces/L/scans/s1/logs?file=workflow.log").json()["content"] == "wf content"
    assert client.get("/api/workspaces/L/scans/s1/logs?file=agents/recon.log").json()["content"] == "agent content"
    assert client.get("/api/workspaces/L/scans/s1/logs?file=nope.log").status_code == 404


def test_scan_report_appends_poc(authed_client, tmp_workspaces):
    """report 拼接 PoC md（--- 分隔 + 代码块保留）。"""
    scan_dir = _scan_with(tmp_workspaces, "P", scan_id="s1")
    dl = scan_dir / "deliverables" / "whitebox"; dl.mkdir(parents=True)
    (dl / "comprehensive_security_assessment_report.md").write_text("# 综合报告\n\n正文")
    (dl / "exploitable_poc_collection.md").write_text(
        "# 可利用漏洞 PoC 集合（白盒）\n\n```bash\ncurl -i -X GET 'https://t/x'\n```")
    body = authed_client.get("/api/workspaces/P/scans/s1/report").text
    assert body.startswith("# 综合报告")
    assert "---" in body
    assert "# 可利用漏洞 PoC 集合（白盒）" in body
    assert "```bash" in body and "curl -i" in body


def test_scan_report_no_report_empty_200(authed_client, tmp_workspaces):
    """scan 存在但无报告产物 -> 200 空文本（非 404）。"""
    _scan_with(tmp_workspaces, "NoReport", scan_id="s1")  # 有 session.json 无 deliverables
    r = authed_client.get("/api/workspaces/NoReport/scans/s1/report")
    assert r.status_code == 200 and r.text == ""


def test_scan_detail_session_data_shape(authed_client, tmp_workspaces):
    """scan 详情 SessionData：metrics.phases/agents + session 嵌套；无 vuln_count/is_correlation。"""
    _scan_with(tmp_workspaces, "A", scan_id="s1", status="completed",
        repo_path="/repo",
        metrics={
            "total_duration_ms": 1000, "total_cost_usd": 1.5,
            "phases": {"recon": {"duration_ms": 1000, "duration_percentage": 100, "cost_usd": 1.5, "agent_count": 1}},
            "agents": {"recon": {"duration_ms": 1000, "cost_usd": 1.5, "success": True, "attempt_number": 1, "model": "x"}},
        },
        session={"id": "A", "status": "completed", "createdAt": "2026-05-29T10:00:00Z"})
    d = authed_client.get("/api/workspaces/A/scans/s1").json()
    assert d["repo_path"] == "/repo"
    assert d["status"] == "completed"
    assert isinstance(d["created_at"], (int, float))
    assert "recon" in d["metrics"]["phases"]
    assert "recon" in d["metrics"]["agents"]
    assert d["metrics"]["total_cost_usd"] == 1.5
    assert d["session"]["id"] == "A"
    assert "vuln_count" not in d and "is_correlation" not in d


def test_scan_detail_recently_active_running(authed_client, tmp_workspaces):
    """scan heartbeat fresh -> status=running，不 500。"""
    import time as _time
    scan_dir = _scan_with(tmp_workspaces, "HostAlive", scan_id="s1", status=None,
                          created_at=_time.time(), completed_at=None)
    (scan_dir / "heartbeat").write_text(f"{_time.time()}\n")
    r = authed_client.get("/api/workspaces/HostAlive/scans/s1")
    assert r.status_code == 200 and r.json()["status"] == "running"


def test_scan_detail_normalizes_legacy_agents(authed_client, tmp_workspaces):
    """scan 详情归一化旧格式 metrics.agents（final_duration_ms/total_cost_usd -> 新 schema）。"""
    _scan_with(tmp_workspaces, "Legacy", scan_id="s1", status="completed",
        metrics={
            "total_duration_ms": 1000, "total_cost_usd": 8.79,
            "phases": {"recon": {"duration_ms": 1000, "duration_percentage": 100.0,
                                 "cost_usd": 8.79, "agent_count": 1}},
            "agents": {"recon": {
                "status": "success",
                "attempts": [{"attempt_number": 1, "duration_ms": 1000, "cost_usd": 8.79,
                              "success": True, "model": "claude-opus-4-7"}],
                "final_duration_ms": 1000, "total_cost_usd": 8.79,
                "model": "claude-opus-4-7", "checkpoint": "abc"}},
        })
    d = authed_client.get("/api/workspaces/Legacy/scans/s1").json()
    a = d["metrics"]["agents"]["recon"]
    assert a["cost_usd"] == 8.79 and a["duration_ms"] == 1000
    assert a["success"] is True and a["attempt_number"] == 1
    assert "final_duration_ms" not in a and "total_cost_usd" not in a and "attempts" not in a
