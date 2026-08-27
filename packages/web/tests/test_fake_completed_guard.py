"""假完成防线（2026-08-27 NodeGoat-20260827-152204 事故）。

combined 扫描死于 precheck（authcheck 3 次超时）+ web/worker 中途重启 → 进程内编排协程
丢失 → orphan_reconciler 委托 _reconcile_combined_scan 收口，其 finally 对「主 workflow
不存在」（precheck 阶段=白盒从未提交）的扫描默认 _ensure_scan_end(status="completed")
→ 假完成：报告全空、用户无从排查，且 completed 不可续跑（resume spec 状态集排除）=死局。

两道后端防线契约：
- A（reconcile 分流）：bb_phase ∈ {precheck,pending} 且主 workflow 不在跑 → 查 authcheck
  workflow（{ws}-{scan_id}-authcheck，对账器此前不认识它）终态——
  FAILED → 收口 failed + bb_failure_detail 落 .authcheck/activity_failures.log 尾部
  （stderr_tail 透出，live 页可见）；RUNNING → 不干预（wf_active，等终态下次再收口）；
  其余 → 收口 interrupted（编排随 web 重启丢失于白盒提交前后）+ bb_reason 落盘。
- B（_ensure_scan_end 保险丝）：status="completed" + combined + bb_phase∈{precheck,pending}
  + completed_agents 空 + 无 deliverables/whitebox 产物 → 拒绝 completed，降级 failed
  （防未来任何新路径再造假完成）。正常完成（有 agent / 有产物 / 黑盒后段 phase /
  非 combined）不受影响。
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from supernova_core.session import SessionManager
from supernova_web.components.scan_manager import ScanManager


@pytest.fixture
def mgr(tmp_path):
    return ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())


def _make_combined(tmp_path, ws="ws", scan_id="scan-1", *, bb_phase="precheck",
                   completed_agents=None, with_deliverables=False,
                   with_scan_end=False, authcheck_failures=""):
    """建组合 scan_dir + session.json + events.ndjson（bb_phase 默认 precheck=事故形态）。

    authcheck_failures 非空时写 .authcheck/activity_failures.log（A 线透出源）。
    """
    scan_dir = tmp_path / ws / "scans" / scan_id
    scan_dir.mkdir(parents=True)
    session = {
        "scan_type": "whitebox", "status": "running", "combined": True,
        "bb_url": "http://t/", "bb_auth_ref": {"profile_id": None},
        "bb_phase": bb_phase, "completed_agents": completed_agents or [],
    }
    (scan_dir / "session.json").write_text(json.dumps(session))
    events = '{"type":"PhaseEvent","phase":"whitebox"}\n'
    if with_scan_end:
        events += '{"type":"scan_end","status":"completed"}\n'
    (scan_dir / "events.ndjson").write_text(events)
    if with_deliverables:
        d = scan_dir / "deliverables" / "whitebox"
        d.mkdir(parents=True)
        (d / "recon_deliverable.md").write_text("# recon\n")
    if authcheck_failures:
        probe = scan_dir / ".authcheck"
        probe.mkdir()
        (probe / "activity_failures.log").write_text(authcheck_failures)
    return scan_dir


def _last_event(scan_dir):
    lines = (scan_dir / "events.ndjson").read_text("utf-8").strip().splitlines()
    return json.loads(lines[-1])


def _status_by_workflow_id(mapping):
    """_query_workflow_status 的假件：按 workflow_id 分发终态。"""
    async def _fake(self_or_none, workflow_id):
        return mapping.get(workflow_id)
    return _fake


# ── A：reconcile 按 bb_phase 分流收口 ──────────────────────────────────────

async def test_reconcile_precheck_authcheck_failed_marks_failed_with_tail(mgr, tmp_path):
    """事故形态：bb_phase=precheck + 主 workflow 不存在 + authcheck FAILED →
    收口 failed；bb_failure_detail 落 activity_failures 尾部并透出到 scan_end.stderr_tail。"""
    scan_dir = _make_combined(tmp_path, authcheck_failures=(
        "2026-08-27 15:42:19 WARNING temporalio.activity: Completing activity as failed\n"
        "asyncio.exceptions.CancelledError\n"))
    fake = _status_by_workflow_id({
        "ws-scan-1": None,            # 主 workflow 不存在（白盒从未提交）
        "ws-scan-1-authcheck": "failed"})
    with patch.object(ScanManager, "_query_workflow_status", fake):
        await mgr._reconcile_combined_scan(scan_dir)
    end = _last_event(scan_dir)
    assert end["type"] == "scan_end" and end["status"] == "failed"
    assert "CancelledError" in end["stderr_tail"]  # authcheck 失败尾部透出
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data.get("status") == "failed"
    assert data.get("bb_failure_point") == "authcheck"
    assert "CancelledError" in data.get("bb_failure_detail", "")


async def test_reconcile_precheck_authcheck_completed_marks_interrupted(mgr, tmp_path):
    """authcheck COMPLETED 但白盒未提交（提交瞬间编排丢失）→ 收口 interrupted +
    bb_reason 落盘（非扫描自身失败，留给 resume 续跑口）。"""
    scan_dir = _make_combined(tmp_path)
    fake = _status_by_workflow_id({
        "ws-scan-1": None, "ws-scan-1-authcheck": "completed"})
    with patch.object(ScanManager, "_query_workflow_status", fake):
        await mgr._reconcile_combined_scan(scan_dir)
    end = _last_event(scan_dir)
    assert end["type"] == "scan_end" and end["status"] == "interrupted"
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data.get("status") == "interrupted"
    assert "编排" in data.get("bb_reason", "")


async def test_reconcile_precheck_authcheck_running_no_scan_end(mgr, tmp_path):
    """authcheck 仍 RUNNING（worker 死了但 workflow 等 start_to_close 超时窗口）→
    不收口（wf_active），等终态后下次 reconcile 再处理。"""
    scan_dir = _make_combined(tmp_path)
    fake = _status_by_workflow_id({
        "ws-scan-1": None, "ws-scan-1-authcheck": "running"})
    with patch.object(ScanManager, "_query_workflow_status", fake):
        await mgr._reconcile_combined_scan(scan_dir)
    assert _last_event(scan_dir)["type"] != "scan_end"
    assert SessionManager(scan_dir.parent).get_status(scan_dir) != "completed"


async def test_reconcile_non_precheck_phase_keeps_completed_default(mgr, tmp_path):
    """回归：bb_phase 已进黑盒后段（run 处理完、workflow 已回收）→ 维持现状默认
    completed 收口（只分流 precheck/pending）。"""
    scan_dir = _make_combined(tmp_path, bb_phase="completed",
                              completed_agents=["pre-recon"])
    fake = _status_by_workflow_id({"ws-scan-1": None})
    with patch.object(ScanManager, "_query_workflow_status", fake):
        await mgr._reconcile_combined_scan(scan_dir)
    end = _last_event(scan_dir)
    assert end["type"] == "scan_end" and end["status"] == "completed"


# ── B：_ensure_scan_end 假完成保险丝 ────────────────────────────────────────

async def test_ensure_scan_end_fuse_demotes_fake_completed(mgr, tmp_path):
    """combined + precheck + 零 agent + 无白盒产物 → 即使调用方传 completed 也降级
    failed + bb_failure_detail 落盘（stderr_tail 透出）。"""
    scan_dir = _make_combined(tmp_path)
    await mgr._ensure_scan_end(scan_dir)  # 默认 status="completed"
    end = _last_event(scan_dir)
    assert end["type"] == "scan_end" and end["status"] == "failed"
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data.get("status") == "failed"
    assert "假完成" in data.get("bb_failure_detail", "") or \
        "bb_failure_detail" in data  # 落盘原因（具体文案实现侧定，断言非空）
    assert data.get("bb_failure_detail")


async def test_ensure_scan_end_fuse_keeps_completed_with_agents(mgr, tmp_path):
    """completed_agents 非空（正常完成的信号）→ 不降级。"""
    scan_dir = _make_combined(tmp_path, bb_phase="completed",
                              completed_agents=["pre-recon", "recon"],
                              with_deliverables=True)
    await mgr._ensure_scan_end(scan_dir)
    assert _last_event(scan_dir)["status"] == "completed"


async def test_ensure_scan_end_fuse_keeps_completed_with_deliverables(mgr, tmp_path):
    """agents 字段缺失但白盒产物存在（旧数据形态）→ 不降级（双信号任一为真即放行）。"""
    scan_dir = _make_combined(tmp_path, bb_phase="pending",
                              completed_agents=None, with_deliverables=True)
    await mgr._ensure_scan_end(scan_dir)
    assert _last_event(scan_dir)["status"] == "completed"


async def test_ensure_scan_end_fuse_ignores_non_combined(mgr, tmp_path):
    """非 combined（纯白盒/纯黑盒）→ 保险丝不介入（零回归）。"""
    scan_dir = _make_combined(tmp_path, bb_phase="precheck")
    (scan_dir / "session.json").write_text(json.dumps({
        "scan_type": "whitebox", "status": "running", "combined": False,
        "completed_agents": []}))
    await mgr._ensure_scan_end(scan_dir)
    assert _last_event(scan_dir)["status"] == "completed"


async def test_ensure_scan_end_fuse_ignores_late_phase(mgr, tmp_path):
    """bb_phase 已是黑盒后段（completed/skipped 等）→ 不降级（黑盒 skipped 是合法完成态）。"""
    scan_dir = _make_combined(tmp_path, bb_phase="skipped", completed_agents=["recon"])
    await mgr._ensure_scan_end(scan_dir)
    assert _last_event(scan_dir)["status"] == "completed"
