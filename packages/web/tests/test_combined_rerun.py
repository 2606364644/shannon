"""组合扫描黑盒续跑（Task 7 / D5）：rerun_blackbox + _run_blackbox_phase suffix 扩展。

核心契约（spec §11.3）：
- 仅 combined + bb_phase=failed 可续跑（零回归：非组合 / 非 failed 拒）。
- 白盒产物必须完好（_whitebox_deliverables_ready）。
- 每次 rerun：bb_rerun_attempts 递增 → workflow_id suffix -bb-rerun-N。
- new_auth 可选：换认证 → 重 dump scan-config.yaml + 预验证新认证。
- 预验证 fail → _mark_bb(failed, auth_failed) + return（不起黑盒）。
- scan_end 不变量：rerun 不重复写（_ensure_scan_end 幂等）。

_run_blackbox_phase suffix 扩展（零回归）：
- 默认 suffix="-bb"（Task 4 既有调用 workflow_id 不变）。
- rerun 传 "-bb-rerun-N"。
"""
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supernova_core.session import SessionManager
from supernova_web.components.scan_manager import ScanManager
from supernova_web.models import ScanRequest


# ── fixture / helpers ──────────────────────────────────────────────────────

@pytest.fixture
def mgr(tmp_path):
    """最小 ScanManager（_ws_config_store/auth/host store 均 None → 走兜底）。"""
    return ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())


def _make_failed_combined_scan_dir(workspaces_dir, ws, scan_id,
                                    bb_rerun_attempts=0, with_deliverables=True,
                                    bb_phase="failed", combined=True,
                                    bb_url="http://target.example/"):
    """建组合扫描 scan_dir（默认 bb_phase=failed + 白盒产物在）。

    workspaces/<ws>/scans/<scan_id>/session.json + deliverables/whitebox/。
    """
    scan_dir = Path(workspaces_dir) / ws / "scans" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    sess = {
        "status": "failed", "scan_type": "whitebox", "created_at": time.time(),
        "web_url": bb_url, "repo_path": "/code/x",
        "combined": combined, "bb_phase": bb_phase,
        "bb_rerun_attempts": bb_rerun_attempts,
        "bb_url": bb_url, "bb_auth_ref": {"profile_id": None},
        "bb_host_mappings": {},
    }
    (scan_dir / "session.json").write_text(json.dumps(sess))
    if with_deliverables:
        wb = scan_dir / "deliverables" / "whitebox"
        wb.mkdir(parents=True, exist_ok=True)
        (wb / "recon_deliverable.md").write_text("recon")
        (wb / "injection_exploitation_queue.json").write_text(
            '{"vulnerabilities":[{"id":1}]}')
    # 旧 scan_end（failed 时 _combined_orchestrator 写的，rerun 需处理）
    (scan_dir / "events.ndjson").write_text(
        '{"type":"scan_end","status":"failed"}\n')
    return scan_dir


def _new_auth_req(**kw) -> ScanRequest:
    """构造 new_auth ScanRequest（组合模式 whitebox + url + inline auth）。"""
    base = {
        "type": "whitebox",
        "url": "http://target.example/",
        "workspace": "ws-a",
        "authentication": {
            "login_type": "form",
            "login_url": "http://target.example/login",
            "credentials": {"username": "new", "password": "new-secret"},
        },
    }
    base.update(kw)
    return ScanRequest(**base)


async def _drain_bg_tasks(mgr):
    """收尾 fire-and-forget orchestrator task，防 pending warning。"""
    for t in list(mgr._orchestrator_tasks.values()):
        if not t.done():
            await t


# ── rerun_blackbox 主路径：failed → -bb-rerun-1 ─────────────────────────────

async def test_rerun_blackbox_failed_fires_rerun1_suffix(mgr, tmp_path):
    """bb_phase=failed + 白盒产物在 → rerun → bb_rerun_attempts=1 +
    _submit_blackbox suffix=-bb-rerun-1 + 跳过白盒（不调 _submit_whitebox）。"""
    ws, scan_id = "ws-a", "s1"
    _make_failed_combined_scan_dir(tmp_path, ws, scan_id)
    bb_handle = MagicMock(); bb_handle.result = AsyncMock(return_value=None)
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=True)), \
         patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)) as sb, \
         patch.object(mgr, "_submit_whitebox", new=AsyncMock()) as sw, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch.object(mgr, "_write_scan_end", new=AsyncMock()), \
         patch.object(mgr, "_mark_bb", new=AsyncMock()):
        await mgr.rerun_blackbox(ws, scan_id, new_auth=None)
        await _drain_bg_tasks(mgr)
        sb.assert_awaited()
        assert sb.call_args.kwargs.get("workflow_id_suffix") == "-bb-rerun-1", (
            "首次续跑 suffix 应为 -bb-rerun-1")
        sw.assert_not_awaited()  # 跳过白盒
    # session bb_rerun_attempts 递增到 1 + bb_phase=running
    scan_dir = mgr._store.get_scan_dir(ws, scan_id)
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data.get("bb_rerun_attempts") == 1
    assert data.get("bb_phase") == "running"


# ── rerun_blackbox 换认证：重 dump scan-config.yaml ─────────────────────────

async def test_rerun_blackbox_with_new_auth_redumps_scan_config(mgr, tmp_path):
    """传 new_auth → _dump_auth_config 被调 + scan-config.yaml 被重写（含新认证）。"""
    ws, scan_id = "ws-a", "s1"
    scan_dir = _make_failed_combined_scan_dir(tmp_path, ws, scan_id)
    bb_handle = MagicMock(); bb_handle.result = AsyncMock(return_value=None)
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=True)) as rc, \
         patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)), \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch.object(mgr, "_write_scan_end", new=AsyncMock()), \
         patch.object(mgr, "_mark_bb", new=AsyncMock()):
        await mgr.rerun_blackbox(ws, scan_id, new_auth=_new_auth_req())
        await _drain_bg_tasks(mgr)
        rc.assert_awaited()  # 预验证新认证
    # scan-config.yaml 被重 dump（_dump_auth_config 真实写入，非 mock）
    cfg = scan_dir / "scan-config.yaml"
    assert cfg.exists(), "new_auth 应触发 scan-config.yaml 重 dump"
    body = cfg.read_text("utf-8")
    assert "new" in body and "new-secret" in body, "新认证凭据应写入 scan-config.yaml"


# ── 多次续跑：N 递增 -bb-rerun-1 / -bb-rerun-2 ──────────────────────────────

async def test_rerun_blackbox_multiple_reruns_increment_N(mgr, tmp_path):
    """两次续跑 → 第一次 -bb-rerun-1，第二次 -bb-rerun-2（N 递增）。"""
    ws, scan_id = "ws-a", "s1"
    _make_failed_combined_scan_dir(tmp_path, ws, scan_id)
    bb_handle = MagicMock(); bb_handle.result = AsyncMock(return_value=None)

    async def _do_rerun():
        with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=True)), \
             patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)) as sb, \
             patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
             patch.object(mgr, "_write_scan_end", new=AsyncMock()), \
             patch.object(mgr, "_mark_bb", new=AsyncMock()):
            await mgr.rerun_blackbox(ws, scan_id, new_auth=None)
            await _drain_bg_tasks(mgr)
            return sb.call_args.kwargs.get("workflow_id_suffix")

    suffix1 = await _do_rerun()
    assert suffix1 == "-bb-rerun-1"
    # 第二次续跑前须重设 bb_phase=failed（实际场景：第二次黑盒也 fail 才能再续）
    scan_dir = mgr._store.get_scan_dir(ws, scan_id)
    SessionManager(scan_dir.parent).update_session(scan_dir, {"bb_phase": "failed"})
    suffix2 = await _do_rerun()
    assert suffix2 == "-bb-rerun-2"
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data.get("bb_rerun_attempts") == 2


# ── 预验证 fail → _mark_bb(failed, auth_failed) + 不起黑盒 ───────────────────

async def test_rerun_blackbox_precheck_fail_marks_auth_failed(mgr, tmp_path):
    """_run_precheck False → _mark_bb(failed, auth_failed) + 不提交黑盒。"""
    ws, scan_id = "ws-a", "s1"
    _make_failed_combined_scan_dir(tmp_path, ws, scan_id)
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=False)), \
         patch.object(mgr, "_submit_blackbox", new=AsyncMock()) as sb, \
         patch.object(mgr, "_mark_bb", new=AsyncMock()) as mark:
        await mgr.rerun_blackbox(ws, scan_id, new_auth=None)
        await _drain_bg_tasks(mgr)
        sb.assert_not_awaited()  # 预验证 fail → 不起黑盒
        marked = [c.args for c in mark.call_args_list]
        assert any(a[1] == "failed" and a[2] == "auth_failed" for a in marked), (
            f"期望 _mark_bb(scan_dir, 'failed', 'auth_failed')，实际: {marked}")


# ── 守卫：非组合 / 非 failed / 无产物 拒续跑（零回归）────────────────────────

async def test_rerun_blackbox_rejects_non_combined(mgr, tmp_path):
    """非组合 scan（combined 缺省）→ ValueError。"""
    ws, scan_id = "ws-a", "s1"
    _make_failed_combined_scan_dir(tmp_path, ws, scan_id, combined=False)
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock()) as sb:
        with pytest.raises(ValueError, match="failed"):
            await mgr.rerun_blackbox(ws, scan_id, new_auth=None)
        sb.assert_not_awaited()


async def test_rerun_blackbox_rejects_non_failed_phase(mgr, tmp_path):
    """bb_phase=running（非 failed）→ ValueError（不能续跑在跑的黑盒）。"""
    ws, scan_id = "ws-a", "s1"
    _make_failed_combined_scan_dir(tmp_path, ws, scan_id, bb_phase="running")
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock()) as sb:
        with pytest.raises(ValueError):
            await mgr.rerun_blackbox(ws, scan_id, new_auth=None)
        sb.assert_not_awaited()


async def test_rerun_blackbox_rejects_when_deliverables_missing(mgr, tmp_path):
    """白盒产物缺失 → ValueError（续跑前白盒产物须完好）。"""
    ws, scan_id = "ws-a", "s1"
    _make_failed_combined_scan_dir(tmp_path, ws, scan_id, with_deliverables=False)
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock()) as sb:
        with pytest.raises(ValueError, match="产物"):
            await mgr.rerun_blackbox(ws, scan_id, new_auth=None)
        sb.assert_not_awaited()


async def test_rerun_blackbox_rejects_unknown_scan(mgr, tmp_path):
    """scan 不存在 → ValueError。"""
    with pytest.raises(ValueError, match="不存在"):
        await mgr.rerun_blackbox("ws-a", "nope", new_auth=None)


# ── _run_blackbox_phase suffix 扩展（零回归 + rerun suffix 透传）────────────

async def test_run_blackbox_phase_default_suffix_bb_zero_regression(mgr, tmp_path):
    """默认 workflow_id_suffix='-bb' → _submit_blackbox 拿到 '-bb'（Task 4 零回归）。"""
    scan_dir = tmp_path / "ws" / "scans" / "s1"; scan_dir.mkdir(parents=True)
    (scan_dir / "session.json").write_text(json.dumps({
        "combined": True, "bb_url": "http://t/", "bb_host_mappings": {}}))
    wb = scan_dir / "deliverables" / "whitebox"; wb.mkdir(parents=True)
    (wb / "recon_deliverable.md").write_text("x")
    (wb / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    bb_handle = MagicMock(); bb_handle.result = AsyncMock(return_value=None)
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)) as sb, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch.object(mgr, "_mark_bb", new=AsyncMock()):
        await mgr._run_blackbox_phase(scan_dir, "ws", "s1", {"profile_id": None})
        assert sb.call_args.kwargs.get("workflow_id_suffix") == "-bb", (
            "默认 suffix 应为 -bb（Task 4 零回归）")


async def test_run_blackbox_phase_rerun_suffix_propagated(mgr, tmp_path):
    """传 workflow_id_suffix='-bb-rerun-1' → _submit_blackbox 拿到 '-bb-rerun-1'。"""
    scan_dir = tmp_path / "ws" / "scans" / "s1"; scan_dir.mkdir(parents=True)
    (scan_dir / "session.json").write_text(json.dumps({
        "combined": True, "bb_url": "http://t/", "bb_host_mappings": {}}))
    wb = scan_dir / "deliverables" / "whitebox"; wb.mkdir(parents=True)
    (wb / "recon_deliverable.md").write_text("x")
    (wb / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')
    bb_handle = MagicMock(); bb_handle.result = AsyncMock(return_value=None)
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)) as sb, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch.object(mgr, "_mark_bb", new=AsyncMock()):
        await mgr._run_blackbox_phase(
            scan_dir, "ws", "s1", {"profile_id": None},
            workflow_id_suffix="-bb-rerun-1")
        assert sb.call_args.kwargs.get("workflow_id_suffix") == "-bb-rerun-1"
