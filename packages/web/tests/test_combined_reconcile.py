"""组合扫描崩溃恢复（Task 5）：_reconcile_combined_scan 按 bb_phase 补接力/补报告/补 scan_end。

核心契约（spec §7.5 崩溃恢复）：
- 非组合扫描 → 立即返回（零回归，纯白盒/纯黑盒恢复路径不变）。
- bb_phase=pending + 白盒 workflow COMPLETED → 补 _run_blackbox_phase（接力）。
- bb_phase=running + 黑盒 workflow COMPLETED → 补 _generate_combined_report（报告）。
- 任意 bb_phase + events 无 scan_end + workflow 不活跃 → _ensure_scan_end 补写（幂等）。
- workflow 仍 RUNNING → 不干预（不写 scan_end，让 temporal 自然完成）。
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from supernova_web.components.scan_manager import ScanManager


# ── fixture ─────────────────────────────────────────────────────────────────
@pytest.fixture
def mgr(tmp_path):
    """最小 ScanManager（workspaces_dir=tmp_path，不真连 temporal）。"""
    return ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())


def _make_scan_dir(tmp_path, ws="ws", scan_id="scan-1", *, combined=True,
                   bb_phase="pending", bb_rerun_attempts=0, with_scan_end=False,
                   whitebox_ready=False):
    """建 scan_dir（<tmp>/<ws>/scans/<scan_id>/）+ session.json + events.ndjson。

    用 scans/ 层级结构（非平铺），保证 ws 从 scan_dir.parent.parent.name 正确派生
    （不复现 bug #4：scan_dir.parent.name 是 "scans" 非 ws）。
    """
    scan_dir = tmp_path / ws / "scans" / scan_id
    scan_dir.mkdir(parents=True)
    session = {
        "scan_type": "whitebox", "status": "running",
        "combined": combined, "bb_phase": bb_phase,
        "bb_url": "http://t/", "bb_auth_ref": {"profile_id": None},
    }
    if bb_rerun_attempts > 0:
        session["bb_rerun_attempts"] = bb_rerun_attempts
    (scan_dir / "session.json").write_text(json.dumps(session))
    events = '{"type":"PhaseEvent","phase":"whitebox"}\n'
    if with_scan_end:
        events += '{"type":"scan_end","status":"completed"}\n'
    (scan_dir / "events.ndjson").write_text(events)
    if whitebox_ready:
        wb = scan_dir / "deliverables" / "whitebox"
        wb.mkdir(parents=True)
        (wb / "recon_deliverable.md").write_text("recon")
        (wb / "injection_exploitation_queue.json").write_text(
            '{"vulnerabilities":[{"id":1}]}')
    return scan_dir


# ── 非组合扫描零回归 ───────────────────────────────────────────────────────
async def test_non_combined_scan_returns_early(mgr, tmp_path):
    """非组合扫描 → _reconcile_combined_scan 立即返回，不调任何组合方法（零回归）。"""
    scan_dir = _make_scan_dir(tmp_path, combined=False)
    with patch.object(mgr, "_run_blackbox_phase", new=AsyncMock()) as rbp, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()) as gcr, \
         patch.object(mgr, "_query_workflow_status", new=AsyncMock()) as qs, \
         patch.object(mgr, "_ensure_scan_end", new=AsyncMock()) as ese:
        await mgr._reconcile_combined_scan(scan_dir)
        rbp.assert_not_awaited()
        gcr.assert_not_awaited()
        qs.assert_not_awaited()
        ese.assert_not_awaited()


# ── bb_phase=pending + 白盒 COMPLETED → 补接力 ──────────────────────────────
async def test_pending_whitebox_completed_kicks_blackbox(mgr, tmp_path):
    """bb_phase=pending + 白盒 workflow COMPLETED → _run_blackbox_phase 被调（补接力）。

    ws/scan_id 从 scan_dir 正确派生（非 scan_dir.parent.name bug）。
    """
    scan_dir = _make_scan_dir(tmp_path, bb_phase="pending", whitebox_ready=True)
    with patch.object(mgr, "_query_workflow_status",
                      new=AsyncMock(return_value="completed")) as qs, \
         patch.object(mgr, "_run_blackbox_phase", new=AsyncMock()) as rbp, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch.object(mgr, "_ensure_scan_end", new=AsyncMock()):
        await mgr._reconcile_combined_scan(scan_dir)
        rbp.assert_awaited_once()
        args = rbp.call_args.args
        assert args[0] == scan_dir          # scan_dir
        assert args[1] == "ws"              # ws（scan_dir.parent.parent.name，非 bug）
        assert args[2] == "scan-1"          # scan_id


# ── bb_phase=running + 黑盒 COMPLETED → 补报告 ──────────────────────────────
async def test_running_blackbox_completed_generates_report(mgr, tmp_path):
    """bb_phase=running + 黑盒 workflow COMPLETED → _generate_combined_report + _mark_bb(completed)。"""
    scan_dir = _make_scan_dir(tmp_path, bb_phase="running")
    with patch.object(mgr, "_query_workflow_status",
                      new=AsyncMock(return_value="completed")), \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()) as gcr, \
         patch.object(mgr, "_run_blackbox_phase", new=AsyncMock()) as rbp, \
         patch.object(mgr, "_mark_bb", new=AsyncMock()) as mark:
        await mgr._reconcile_combined_scan(scan_dir)
        gcr.assert_awaited_once_with(scan_dir)
        rbp.assert_not_awaited()
        # 标 bb_phase=completed（报告补完 → 终态）
        marked = [c.args for c in mark.call_args_list]
        assert any(a[1] == "completed" for a in marked), \
            f"期望 _mark_bb(scan_dir, 'completed')，实际: {marked}"


# ── bb_phase=running + bb_rerun_attempts=2 → 查 -bb-rerun-2 workflow ─────────
async def test_running_rerun_queries_rerun_workflow_id(mgr, tmp_path):
    """bb_phase=running + bb_rerun_attempts=2 → _query_workflow_status 查
    {ws}-{scan_id}-bb-rerun-2（非首跑 -bb）。"""
    scan_dir = _make_scan_dir(tmp_path, bb_phase="running", bb_rerun_attempts=2)
    captured_ids = []

    async def _capture(wf_id):
        captured_ids.append(wf_id)
        return "completed"

    with patch.object(mgr, "_query_workflow_status", new=_capture), \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch.object(mgr, "_mark_bb", new=AsyncMock()):
        await mgr._reconcile_combined_scan(scan_dir)
    assert captured_ids == ["ws-scan-1-bb-rerun-2"], \
        f"期望查 -bb-rerun-2 workflow，实际: {captured_ids}"


# ── 白盒/黑盒仍 RUNNING → 不干预（不调接力/报告，不写 scan_end）─────────────
async def test_pending_whitebox_running_no_intervention(mgr, tmp_path):
    """bb_phase=pending + 白盒 RUNNING → 不调 _run_blackbox_phase + 不调 _ensure_scan_end
    （workflow 仍活跃，让 temporal 自然完成）。"""
    scan_dir = _make_scan_dir(tmp_path, bb_phase="pending")
    with patch.object(mgr, "_query_workflow_status",
                      new=AsyncMock(return_value="running")), \
         patch.object(mgr, "_run_blackbox_phase", new=AsyncMock()) as rbp, \
         patch.object(mgr, "_ensure_scan_end", new=AsyncMock()) as ese:
        await mgr._reconcile_combined_scan(scan_dir)
        rbp.assert_not_awaited()
        ese.assert_not_awaited()  # workflow 活跃 → 不补 scan_end


async def test_running_blackbox_running_no_intervention(mgr, tmp_path):
    """bb_phase=running + 黑盒 RUNNING → 不调 _generate_combined_report + 不写 scan_end。"""
    scan_dir = _make_scan_dir(tmp_path, bb_phase="running")
    with patch.object(mgr, "_query_workflow_status",
                      new=AsyncMock(return_value="running")), \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()) as gcr, \
         patch.object(mgr, "_ensure_scan_end", new=AsyncMock()) as ese:
        await mgr._reconcile_combined_scan(scan_dir)
        gcr.assert_not_awaited()
        ese.assert_not_awaited()


# ── events 无 scan_end + workflow 不活跃 → _ensure_scan_end 补写 ─────────────
async def test_ensures_scan_end_when_absent(mgr, tmp_path):
    """bb_phase=pending + 白盒 not-found + events 无 scan_end → _ensure_scan_end 补写。"""
    scan_dir = _make_scan_dir(tmp_path, bb_phase="pending", with_scan_end=False)
    with patch.object(mgr, "_query_workflow_status",
                      new=AsyncMock(return_value=None)), \
         patch.object(mgr, "_ensure_scan_end", new=AsyncMock()) as ese:
        await mgr._reconcile_combined_scan(scan_dir)
        ese.assert_awaited_once_with(scan_dir)


# ── events 有 scan_end → _ensure_scan_end 幂等 no-op（不写第二条）────────────
async def test_scan_end_present_no_double_write(mgr, tmp_path):
    """events 已有 scan_end → _write_scan_end 不被调（_ensure_scan_end 幂等 no-op）。"""
    scan_dir = _make_scan_dir(tmp_path, bb_phase="running", with_scan_end=True)
    with patch.object(mgr, "_query_workflow_status",
                      new=AsyncMock(return_value="completed")), \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch.object(mgr, "_mark_bb", new=AsyncMock()), \
         patch.object(mgr, "_write_scan_end", new=AsyncMock()) as ws_end:
        await mgr._reconcile_combined_scan(scan_dir)
        ws_end.assert_not_awaited()  # 已有 scan_end → 幂等 no-op


# ── precheck 阶段：authcheck + 白盒完成 → 补接力 ────────────────────────────
async def test_precheck_advances_when_authcheck_and_whitebox_completed(mgr, tmp_path):
    """bb_phase=precheck + authcheck COMPLETED + 白盒 COMPLETED → 补 _run_blackbox_phase。"""
    scan_dir = _make_scan_dir(tmp_path, bb_phase="precheck", whitebox_ready=True)
    statuses = {"ws-scan-1-authcheck": "completed", "ws-scan-1": "completed"}

    async def _qs(wf_id):
        return statuses.get(wf_id)

    with patch.object(mgr, "_query_workflow_status", new=_qs), \
         patch.object(mgr, "_run_blackbox_phase", new=AsyncMock()) as rbp:
        await mgr._reconcile_combined_scan(scan_dir)
        rbp.assert_awaited_once()


async def test_precheck_authcheck_running_no_intervention(mgr, tmp_path):
    """bb_phase=precheck + authcheck RUNNING → 不干预（预验证仍跑）。"""
    scan_dir = _make_scan_dir(tmp_path, bb_phase="precheck")
    statuses = {"ws-scan-1-authcheck": "running"}

    async def _qs(wf_id):
        return statuses.get(wf_id)

    with patch.object(mgr, "_query_workflow_status", new=_qs), \
         patch.object(mgr, "_run_blackbox_phase", new=AsyncMock()) as rbp, \
         patch.object(mgr, "_ensure_scan_end", new=AsyncMock()) as ese:
        await mgr._reconcile_combined_scan(scan_dir)
        rbp.assert_not_awaited()
        ese.assert_not_awaited()


# ── orphan_reconciler 接入：reconcile_orphaned 传 scan_manager → 调组合恢复 ─
async def test_reconcile_orphaned_delegates_combined_to_scan_manager(tmp_path):
    """reconcile_orphaned(scan_manager=mgr) 对组合孤儿 → 调 mgr._reconcile_combined_scan
    （而非直接写 scan_end=interrupted，避免与组合接力 scan_end 冲突）。"""
    from supernova_web.components.orphan_reconciler import reconcile_orphaned
    scan_dir = tmp_path / "ws" / "scans" / "scan-1"
    scan_dir.mkdir(parents=True)
    (scan_dir / "session.json").write_text(json.dumps({
        "scan_type": "whitebox", "status": "running",
        "combined": True, "bb_phase": "pending",
    }))
    (scan_dir / "events.ndjson").write_text('{"type":"PhaseEvent"}\n')

    fake_mgr = AsyncMock()  # scan_manager mock
    with patch("supernova_web.components.orphan_reconciler.is_scan_alive",
               return_value=False), \
         patch("supernova_web.components.orphan_reconciler._workflow_still_running",
               new=AsyncMock(return_value=False)):
        await reconcile_orphaned(scan_dir, False, scan_manager=fake_mgr)
        fake_mgr._reconcile_combined_scan.assert_awaited_once_with(scan_dir)


async def test_reconcile_orphaned_non_combined_writes_scan_end_as_before(tmp_path):
    """非组合孤儿 → reconcile_orphaned 原有行为不变（写 scan_end=interrupted）。"""
    from supernova_web.components.orphan_reconciler import reconcile_orphaned, _has_scan_end
    scan_dir = tmp_path / "ws" / "scans" / "scan-1"
    scan_dir.mkdir(parents=True)
    (scan_dir / "session.json").write_text(json.dumps({
        "scan_type": "whitebox", "status": "running",  # 无 combined 字段 → 非组合
    }))
    (scan_dir / "events.ndjson").write_text('{"type":"PhaseEvent"}\n')

    fake_mgr = AsyncMock()
    with patch("supernova_web.components.orphan_reconciler.is_scan_alive",
               return_value=False), \
         patch("supernova_web.components.orphan_reconciler._workflow_still_running",
               new=AsyncMock(return_value=False)):
        result = await reconcile_orphaned(scan_dir, False, scan_manager=fake_mgr)
        assert result is True  # 写了 scan_end
        assert _has_scan_end(scan_dir / "events.ndjson")
        fake_mgr._reconcile_combined_scan.assert_not_awaited()  # 非组合 → 不调
