"""组合扫描 t0 认证预验证（Task 3 / D4）：start 组合分支插 _run_precheck 链。

核心契约（spec §7.2 D4）：
- pass → _submit_whitebox 被调（继续白盒 + 接力编排）。
- fail → _submit_whitebox 未调 + session bb_phase=failed/bb_reason=auth_failed
  + scan_end 落盘（fail-fast，不提交白盒）。
- _run_precheck 复用 AuthValidationWorkflow，独立 events 文件 authcheck-events.ndjson
  （不污染主 events 流——预验证 finalize 可能写 scan_end，混入主 events 会提前触发 _watch 退出）。
- session 持久化 combined/bb_url/bb_host_mappings/bb_auth_ref/bb_phase（Task 4 _run_blackbox_phase
  读 bb_url/bb_host_mappings；Task 7 重跑解析 bb_auth_ref）。D2：bb_auth_ref 只存 profile_id 引用。
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from supernova_core.session import SessionManager
from supernova_web.components.scan_manager import ScanManager
from supernova_web.models import ScanRequest


# ── fixture ─────────────────────────────────────────────────────────────────

def _combined_req(**kw) -> ScanRequest:
    """组合扫描请求（whitebox + url + inline auth = 组合模式）。"""
    base = {
        "type": "whitebox",
        "url": "http://target.example/",
        "source": {"kind": "repo", "value": "demo-repo"},
        "workspace": "ws-a",
        "authentication": {
            "login_type": "form",
            "login_url": "http://target.example/login",
            "credentials": {"username": "a", "password": "secret"},
        },
    }
    base.update(kw)
    return ScanRequest(**base)


@pytest.fixture
def mgr(tmp_path):
    """最小 ScanManager（_ws_config_store/auth/host store 均 None → 走兜底）。"""
    return ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())


def _wire_start_mocks(mgr, monkeypatch):
    """给 start 打通用 mock（_check_temporal / _resolve_inputs / _watch）。"""
    async def noop(self, *a, **kw):
        return None
    monkeypatch.setattr(ScanManager, "_check_temporal", noop)
    monkeypatch.setattr(ScanManager, "_watch", noop)

    async def fake_resolve_inputs(self, req):
        return ("/fake/repo", None)
    monkeypatch.setattr(ScanManager, "_resolve_inputs", fake_resolve_inputs)


async def _drain_bg_tasks(mgr):
    """收尾 fire-and-forget task（_watch noop + _combined_orchestrator mock），防 pending warning。"""
    for t in list(mgr._tasks.values()) + list(mgr._orchestrator_tasks.values()):
        if not t.done():
            await t


# ── start 组合分支：pass 路径 ────────────────────────────────────────────────

async def test_start_combined_precheck_pass_submits_whitebox(mgr, monkeypatch):
    """pass：_run_precheck True → _submit_whitebox 被调；session 组合字段持久化。"""
    _wire_start_mocks(mgr, monkeypatch)
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=True)) as rc, \
         patch.object(mgr, "_submit_whitebox", new=AsyncMock(return_value=object())) as sw, \
         patch.object(mgr, "_combined_orchestrator", new=AsyncMock()) as orch:
        ws, scan_id = await mgr.start(_combined_req())
        await _drain_bg_tasks(mgr)
        rc.assert_awaited()                           # 预验证被调
        sw.assert_awaited()                           # pass → 提交白盒
        orch.assert_awaited()                         # 接力编排登记
        scan_dir = mgr._store.get_scan_dir(ws, scan_id)
        data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
        assert data.get("combined") is True
        assert data.get("bb_url") == "http://target.example/"
        assert data.get("bb_auth_ref") == {"profile_id": None}  # inline → 无 profile_id
        assert data.get("bb_phase") != "precheck"     # pass 后转出 precheck 阶段


# ── start 组合分支：fail 路径（fail-fast）────────────────────────────────────

async def test_start_combined_precheck_fail_skips_whitebox_and_marks_failed(mgr, monkeypatch):
    """fail：_run_precheck False → _submit_whitebox 未调 + 不起编排；session bb_phase=failed/
    bb_reason=auth_failed（真实 session 状态，非 mock 断言）；scan_end 落盘。"""
    _wire_start_mocks(mgr, monkeypatch)
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=False)) as rc, \
         patch.object(mgr, "_submit_whitebox", new=AsyncMock(return_value=object())) as sw, \
         patch.object(mgr, "_combined_orchestrator", new=AsyncMock()) as orch:
        ws, scan_id = await mgr.start(_combined_req())
        await _drain_bg_tasks(mgr)
        rc.assert_awaited()                           # 预验证被调
        sw.assert_not_awaited()                       # fail → 不提交白盒
        orch.assert_not_awaited()                     # fail → 不起编排
        scan_dir = mgr._store.get_scan_dir(ws, scan_id)
        data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
        assert data.get("bb_phase") == "failed"       # 真实 session 状态
        assert data.get("bb_reason") == "auth_failed"
        # scan_end 落盘（_ensure_scan_end 写 events.ndjson 标记终态）
        assert (scan_dir / "events.ndjson").exists()
        end_line = (scan_dir / "events.ndjson").read_text("utf-8").strip().splitlines()[-1]
        assert json.loads(end_line)["type"] == "scan_end"


# ── start 组合分支：HOST 映射 + profile 引用持久化 ───────────────────────────

async def test_start_combined_persists_host_mappings_and_profile_ref(mgr, monkeypatch):
    """bb_host_mappings + bb_auth_ref（profile 模式）持久化到 session.json（Task 4/7 读）。"""
    _wire_start_mocks(mgr, monkeypatch)
    mappings = {"target.example": "10.0.0.5"}
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=True)), \
         patch.object(mgr, "_submit_whitebox", new=AsyncMock(return_value=object())), \
         patch.object(mgr, "_combined_orchestrator", new=AsyncMock()), \
         patch.object(mgr, "_resolve_host_mappings", new=AsyncMock(return_value=mappings)) as rhm, \
         patch.object(mgr, "_dump_auth_config", new=AsyncMock(return_value="/cfg.yaml")):
        ws, scan_id = await mgr.start(_combined_req(
            auth_profile_id="prof_1", auth_credential_id="cred_a",
            authentication=None))  # profile 模式（覆盖 inline 默认）
        await _drain_bg_tasks(mgr)
        rhm.assert_awaited()
        scan_dir = mgr._store.get_scan_dir(ws, scan_id)
        data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
        assert data.get("bb_host_mappings") == mappings
        # D2: bb_auth_ref 只存 profile_id 引用，不含明文
        assert data.get("bb_auth_ref") == {"profile_id": "prof_1",
                                           "cred_id": "cred_a",
                                           "cred_ids": None}
        dumped = json.dumps(data.get("bb_auth_ref"))
        assert "secret" not in dumped and "password" not in dumped


# ── _run_precheck 直测：复用 AuthValidationWorkflow + 独立 events ─────────────

async def test_run_precheck_returns_true_on_auth_success(mgr, tmp_path, monkeypatch):
    """_run_precheck：AuthValidationResult(success=True) → True。复用 AuthValidationWorkflow。"""
    from supernova_core.services.validate_authentication import AuthValidationResult
    scan_dir = tmp_path / "scans" / "demo"; scan_dir.mkdir(parents=True)
    captured = {}

    class _FakeHandle:
        async def result(self):
            return AuthValidationResult(success=True)

    class _FakeClient:
        async def start_workflow(self, fn, inp, **kw):
            captured["fn"] = fn
            captured["inp"] = inp
            captured["kw"] = kw
            return _FakeHandle()

    async def fake_connect(addr):
        return _FakeClient()
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect", fake_connect)
    monkeypatch.setattr(mgr, "_resolve_provider_config", lambda ws: {"api_key": "k"})

    ok = await mgr._run_precheck(scan_dir, "ws-a", "demo",
                                 "http://target.example/", "/cfg/scan-config.yaml")
    assert ok is True
    # 复用 AuthValidationWorkflow（不 reinvent）
    fn_name = getattr(captured["fn"], "__qualname__", "")
    assert "AuthValidationWorkflow" in fn_name
    # 独立 events 文件（不污染主 events 流）
    assert captured["inp"].event_file == str(scan_dir / "authcheck-events.ndjson")
    assert captured["inp"].web_url == "http://target.example/"
    assert captured["inp"].config_path == "/cfg/scan-config.yaml"


async def test_run_precheck_returns_false_on_auth_failure(mgr, tmp_path, monkeypatch):
    """_run_precheck：AuthValidationResult(success=False) → False（fail-fast 判定）。"""
    from supernova_core.services.validate_authentication import AuthValidationResult
    scan_dir = tmp_path / "scans" / "demo"; scan_dir.mkdir(parents=True)

    class _FakeHandle:
        async def result(self):
            return AuthValidationResult(success=False, failure_point="username_or_password")

    class _FakeClient:
        async def start_workflow(self, fn, inp, **kw):
            return _FakeHandle()

    async def fake_connect(addr):
        return _FakeClient()
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect", fake_connect)
    monkeypatch.setattr(mgr, "_resolve_provider_config", lambda ws: {"api_key": "k"})

    ok = await mgr._run_precheck(scan_dir, "ws-a", "demo",
                                 "http://target.example/", "/cfg/scan-config.yaml")
    assert ok is False
