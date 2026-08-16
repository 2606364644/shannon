"""precheck 失败可见性：verdict 详情落盘 + 透出（2026-08-16 NodeGoat 登录地址不可达事故）。

用户此前只能看到「失败」徽章 + "combined failed"，真实原因（如 "Target unreachable:
TCP connect ... refused"）埋在 authcheck-events.ndjson 的 LLM turn 里。契约：
- _run_precheck 确定性拒绝 → session 落 bb_failure_point/bb_failure_detail。
- _ensure_scan_end(failed) → scan_end.stderr_tail 拼上 bb_failure_detail（或 bb_reason）。
- _scan_detail → 响应透出 bb_failure_point/bb_failure_detail。
- update_blackbox_run(extra) → run session + 任务 bb_runs[] 条目并入（run 级横幅用）。
"""
import json
from unittest.mock import patch

from supernova_core.session import SessionManager
from supernova_web.components.scan_manager import ScanManager
from supernova_web.components.scan_store import ScanStore


def _mgr(tmp_path):
    return ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())


def _wire_fake_auth_result(monkeypatch, mgr, result):
    """把 temporal Client.connect 换成返固定 AuthValidationResult 的假件。"""

    class _FakeHandle:
        async def result(self):
            return result

    class _FakeClient:
        async def start_workflow(self, fn, inp, **kw):
            return _FakeHandle()

    async def fake_connect(addr):
        return _FakeClient()

    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect", fake_connect)
    monkeypatch.setattr(mgr, "_resolve_provider_config", lambda ws: {"api_key": "k"})


# ── _run_precheck：确定性拒绝落 verdict ─────────────────────────────────────

async def test_run_precheck_failure_persists_verdict(tmp_path, monkeypatch):
    """确定性拒绝 → False + session bb_failure_point/bb_failure_detail（API/横幅消费）。"""
    from supernova_core.services.validate_authentication import AuthValidationResult
    mgr = _mgr(tmp_path)
    scan_dir = tmp_path / "scans" / "demo"; scan_dir.mkdir(parents=True)
    _wire_fake_auth_result(monkeypatch, mgr, AuthValidationResult(
        success=False, failure_point="username_or_password",
        failure_detail="Target unreachable: TCP connect to 192.168.100.206:4000 refused"))

    ok = await mgr._run_precheck(scan_dir, "ws", "demo",
                                 "http://target.example/", "/cfg/scan-config.yaml")
    assert ok is False
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data.get("bb_failure_point") == "username_or_password"
    assert "TCP connect to 192.168.100.206:4000 refused" in data.get("bb_failure_detail")


async def test_run_precheck_success_writes_no_failure_fields(tmp_path, monkeypatch):
    """pass 路径不落失败键（横幅据此区分，避免旧值残留误报）。"""
    from supernova_core.services.validate_authentication import AuthValidationResult
    mgr = _mgr(tmp_path)
    scan_dir = tmp_path / "scans" / "demo"; scan_dir.mkdir(parents=True)
    SessionManager(scan_dir.parent).update_session(scan_dir, {"status": "running"})
    _wire_fake_auth_result(monkeypatch, mgr, AuthValidationResult(success=True))

    ok = await mgr._run_precheck(scan_dir, "ws", "demo",
                                 "http://target.example/", "/cfg/scan-config.yaml")
    assert ok is True
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert "bb_failure_point" not in data
    assert "bb_failure_detail" not in data


# ── _ensure_scan_end：失败 tail 透出 ────────────────────────────────────────

def _last_event(scan_dir):
    lines = (scan_dir / "events.ndjson").read_text("utf-8").strip().splitlines()
    return json.loads(lines[-1])


async def test_ensure_scan_end_failed_appends_failure_detail(tmp_path):
    mgr = _mgr(tmp_path)
    scan_dir = tmp_path / "scans" / "demo"; scan_dir.mkdir(parents=True)
    SessionManager(scan_dir.parent).update_session(scan_dir, {
        "bb_failure_detail": "Target unreachable: TCP connect refused"})

    await mgr._ensure_scan_end(scan_dir, status="failed")
    end = _last_event(scan_dir)
    assert end["type"] == "scan_end" and end["status"] == "failed"
    assert "Target unreachable: TCP connect refused" in end["stderr_tail"]


async def test_ensure_scan_end_failed_falls_back_to_reason(tmp_path):
    """无 bb_failure_detail（如白盒编排失败）→ 退 bb_reason；两者皆无 → 原默认串。"""
    mgr = _mgr(tmp_path)
    scan_dir = tmp_path / "scans" / "a"; scan_dir.mkdir(parents=True)
    SessionManager(scan_dir.parent).update_session(scan_dir, {"bb_reason": "whitebox workflow failed"})
    await mgr._ensure_scan_end(scan_dir, status="failed")
    assert "whitebox workflow failed" in _last_event(scan_dir)["stderr_tail"]

    scan_dir2 = tmp_path / "scans" / "b"; scan_dir2.mkdir(parents=True)
    await mgr._ensure_scan_end(scan_dir2, status="failed")
    assert _last_event(scan_dir2)["stderr_tail"] == "combined failed"


# ── _scan_detail API 透出 ──────────────────────────────────────────────────

def test_scan_detail_exposes_bb_failure_fields(authed_client, tmp_workspaces):
    scan_dir = tmp_workspaces / "WS" / "scans" / "s1"
    scan_dir.mkdir(parents=True)
    (scan_dir / "session.json").write_text(json.dumps({
        "status": "failed", "scan_type": "whitebox", "created_at": 1780000000.0,
        "web_url": "http://e", "repo_path": "/code", "owner": "web",
        "combined": True, "bb_phase": "failed", "bb_reason": "auth_failed",
        "bb_failure_point": "username_or_password",
        "bb_failure_detail": "Target unreachable: TCP connect refused"}))

    d = authed_client.get("/api/workspaces/WS/scans/s1").json()
    assert d["status"] == "failed"
    assert d["bb_reason"] == "auth_failed"
    assert d["bb_failure_point"] == "username_or_password"
    assert "TCP connect refused" in d["bb_failure_detail"]


# ── update_blackbox_run：extra 并入 run session + bb_runs[] 条目 ───────────

def test_update_blackbox_run_extra_merges_run_session_and_index(tmp_path):
    store = ScanStore(tmp_path)
    wb_id, wb_dir = store.create_scan("WS", "http://e", "/code/x")
    run_id, run_dir = store.create_blackbox_run("WS", wb_id)

    store.update_blackbox_run(
        "WS", wb_id, run_id, status="failed", phase="failed",
        reason="auth_failed",
        extra={"bb_failure_point": "username_or_password",
               "bb_failure_detail": "Target unreachable: refused"})

    run_sess = json.loads((run_dir / "session.json").read_text())
    assert run_sess["bb_failure_point"] == "username_or_password"
    assert "refused" in run_sess["bb_failure_detail"]
    entry = next(r for r in store.list_blackbox_runs("WS", wb_id)
                 if r["run_id"] == run_id)
    assert entry["reason"] == "auth_failed"
    assert "refused" in entry["bb_failure_detail"]
