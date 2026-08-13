"""组合扫描 resume/cancel 按 bb_phase + bb_rerun_attempts 分阶段（Task 6, spec §11.4/§11.5）。

零回归：combined 分支仅在 session.combined 真时触发；纯白盒/纯黑盒 resume/cancel 不变。

workflow_id 算法（spec §7.6 + §11，与 _reconcile_combined_scan 同口径）：
- 白盒（含 pending/precheck resume）：{ws}-{scan_id}[-resume-N]（_resolve_workflow_id 算）。
- 黑盒首跑：{ws}-{scan_id}[-resume-N]-bb。
- 黑盒续跑：{ws}-{scan_id}[-resume-N]-bb-rerun-{N}（N=bb_rerun_attempts）。

resume 语义分阶段：
- pending/precheck → 重交白盒 workflow（-resume-N 后缀，复用 _submit_whitebox）+ 重启完整
  _combined_orchestrator（接力续跑：白盒完成后跑黑盒 + 报告）。
- running → 黑盒 workflow 仍在 Temporal 跑（scan_manager 进程死了，Temporal 留活）→
  re-attach handle（get_workflow_handle，不重 submit）+ 附仅做报告的编排 task。
- 所有分支 resume 前 _strip_trailing_scan_end（清旧中断 scan_end，让 _watch 能 tail）。

cancel 语义分阶段：按 bb_phase/bb_rerun_attempts 算 workflow_id → re-attach + handle.cancel()；
bb_phase=pending → 白盒 workflow_id；running → 黑盒 -bb/-bb-rerun-N。orchestrator task 一并取消。
"""
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supernova_web.components.scan_manager import ScanManager
from supernova_web.components.scan_store import ScanStore


# ── fixture / helpers ──────────────────────────────────────────────────────

def _make_combined_scan_dir(workspaces_dir, ws, scan_id, bb_phase="pending",
                            bb_rerun_attempts=0, status="interrupted",
                            resume_attempts=None):
    """建组合扫描 scan_dir: workspaces/<ws>/scans/<scan_id>/session.json。

    combined=True 必备字段：bb_phase/bb_rerun_attempts/bb_url/bb_auth_ref。
    resume_attempts 用于控制 _resolve_workflow_id 的 -resume-N 后缀（None=空 list=N=0）。
    """
    scan_dir = Path(workspaces_dir) / ws / "scans" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    sess = {
        "status": status, "scan_type": "whitebox", "created_at": time.time(),
        "web_url": "http://t/", "repo_path": "/code/x",
        "combined": True, "bb_phase": bb_phase,
        "bb_rerun_attempts": bb_rerun_attempts,
        "bb_url": "http://t/", "bb_auth_ref": {"profile_id": None},
        "resumeAttempts": resume_attempts or [],
    }
    (scan_dir / "session.json").write_text(json.dumps(sess))
    return scan_dir


def _patch_temporal_ok(monkeypatch, mgr):
    async def _ok():
        return None
    monkeypatch.setattr(mgr, "_check_temporal", _ok)


def _patch_client(monkeypatch, start_handle=None, get_handle=None):
    """mock Client.connect → mock_client（start_workflow + get_workflow_handle）。"""
    _start_handle = start_handle or MagicMock()
    _get_handle = get_handle or AsyncMock()  # handle.cancel 是 async
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=_start_handle)
    # get_workflow_handle 是 sync 方法（返 handle），不 await。
    mock_client.get_workflow_handle = MagicMock(return_value=_get_handle)
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect",
                       AsyncMock(return_value=mock_client))
    return mock_client


# ── resume: bb_phase=pending → 白盒 -resume-N + 重启完整编排 ─────────────────

@pytest.mark.asyncio
async def test_resume_combined_pending_submits_whitebox_and_restarts_orchestrator(
        tmp_path, monkeypatch):
    """bb_phase=pending → resume 白盒 {ws}-{scan_id}-resume-1 + 重启 _combined_orchestrator。

    白盒走 _submit_whitebox（_resolve_workflow_id 算 -resume-N 后缀，复用既有 resume 语义）。
    编排 task 登记进 _orchestrator_tasks（接力续跑：白盒完成后跑黑盒 + 报告）。
    """
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    _patch_temporal_ok(monkeypatch, mgr)
    mock_client = _patch_client(monkeypatch)
    _make_combined_scan_dir(tmp_path, "WS", "s1", bb_phase="pending")
    scan_key = ("WS", "s1")

    with patch.object(mgr, "_watch", new=AsyncMock()), \
         patch.object(mgr, "_combined_orchestrator", new=AsyncMock()) as orch_mock:
        await mgr.resume("WS", "s1")

    # 白盒 workflow 提交（-resume-1 后缀）
    call = mock_client.start_workflow.call_args
    assert call.kwargs["id"] == "WS-s1-resume-1", (
        "pending resume 应提交白盒 -resume-1 workflow_id")
    # 编排 task 重启（_orchestrator_tasks 登记）
    assert scan_key in mgr._orchestrator_tasks, "应重启 _combined_orchestrator task"
    # _combined_orchestrator 被调（create_task 调用即记 call_args；scan_key, handle, scan_dir, req）
    assert orch_mock.call_args is not None, "_combined_orchestrator 应被调（重启编排 task）"
    assert orch_mock.call_args.args[0] == scan_key
    # handle 登记（供 _watch / cancel）
    assert scan_key in mgr._handles


@pytest.mark.asyncio
async def test_resume_combined_precheck_also_resumes_whitebox(tmp_path, monkeypatch):
    """bb_phase=precheck 同 pending（白盒阶段中断，resume 白盒 + 重启编排）。"""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    _patch_temporal_ok(monkeypatch, mgr)
    mock_client = _patch_client(monkeypatch)
    _make_combined_scan_dir(tmp_path, "WS", "s1", bb_phase="precheck")

    with patch.object(mgr, "_watch", new=AsyncMock()), \
         patch.object(mgr, "_combined_orchestrator", new=AsyncMock()):
        await mgr.resume("WS", "s1")

    call = mock_client.start_workflow.call_args
    assert call.kwargs["id"] == "WS-s1-resume-1"


# ── resume: latest run running → re-attach 黑盒 -bb-{K} ──────────────────────

@pytest.mark.asyncio
async def test_resume_combined_running_reattaches_bb_run_workflow(
        tmp_path, monkeypatch):
    """latest run(run-1) bb_phase=running → re-attach {ws}-{scan_id}-bb-1（不重 submit）
    + 附仅做报告的编排 task（接力已发生，不重复 submit 黑盒）。"""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    _patch_temporal_ok(monkeypatch, mgr)
    mock_client = _patch_client(monkeypatch)
    _make_combined_scan_dir(tmp_path, "WS", "s1")
    store = ScanStore(tmp_path)
    store.create_blackbox_run("WS", "s1")  # run-1
    store.update_blackbox_run("WS", "s1", "run-1", phase="running", status="running")
    scan_key = ("WS", "s1")

    with patch.object(mgr, "_watch", new=AsyncMock()), \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()):
        await mgr.resume("WS", "s1")

    # 不重提交（黑盒 workflow 仍在 Temporal 跑，只 re-attach handle）
    mock_client.start_workflow.assert_not_awaited()
    mock_client.get_workflow_handle.assert_called_with("WS-s1-bb-1")
    # 报告编排 task 登记
    assert scan_key in mgr._orchestrator_tasks
    assert scan_key in mgr._handles  # re-attached handle 登记


# ── resume: latest run=run-2 running → re-attach 黑盒 -bb-2 ──────────────────

@pytest.mark.asyncio
async def test_resume_combined_running_run2_reattaches_bb_run2(
        tmp_path, monkeypatch):
    """latest run(run-2) running → re-attach {ws}-{scan_id}-bb-2（版本化 run K）。"""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    _patch_temporal_ok(monkeypatch, mgr)
    mock_client = _patch_client(monkeypatch)
    _make_combined_scan_dir(tmp_path, "WS", "s1")
    store = ScanStore(tmp_path)
    store.create_blackbox_run("WS", "s1")  # run-1
    store.create_blackbox_run("WS", "s1")  # run-2
    store.update_blackbox_run("WS", "s1", "run-2", phase="running", status="running")

    with patch.object(mgr, "_watch", new=AsyncMock()), \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()):
        await mgr.resume("WS", "s1")

    mock_client.start_workflow.assert_not_awaited()
    mock_client.get_workflow_handle.assert_called_with("WS-s1-bb-2")


# ── resume: _strip_trailing_scan_end（所有分支）──────────────────────────────

@pytest.mark.asyncio
async def test_resume_combined_strips_trailing_scan_end(tmp_path, monkeypatch):
    """resume 前 _strip_trailing_scan_end（running 分支例证：旧 scan_end 必须剥掉，
    否则 _watch 见旧 scan_end 立即退出，无法跟踪 re-attach 的黑盒 workflow）。"""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    _patch_temporal_ok(monkeypatch, mgr)
    _patch_client(monkeypatch)
    scan_dir = _make_combined_scan_dir(tmp_path, "WS", "s1")
    store = ScanStore(tmp_path)
    store.create_blackbox_run("WS", "s1")
    store.update_blackbox_run("WS", "s1", "run-1", phase="running", status="running")
    (scan_dir / "events.ndjson").write_text(
        '{"type":"log","msg":"中途"}\n'
        '{"type":"scan_end","status":"interrupted"}\n')

    with patch.object(mgr, "_watch", new=AsyncMock()), \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()):
        await mgr.resume("WS", "s1")

    lines = (scan_dir / "events.ndjson").read_text().splitlines()
    assert not any('"scan_end"' in l for l in lines), "旧 scan_end 必须剥掉"
    assert any('"log"' in l for l in lines), "普通事件保留"


# ── cancel: bb_phase=pending → 白盒 workflow + 取消编排 ──────────────────────

@pytest.mark.asyncio
async def test_cancel_combined_pending_terminates_whitebox_and_orchestrator(
        tmp_path, monkeypatch):
    """bb_phase=pending → cancel 白盒 {ws}-{scan_id} + 取消编排 task。"""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    mock_handle = AsyncMock()  # handle.cancel 是 async
    mock_client = AsyncMock()
    mock_client.get_workflow_handle = MagicMock(return_value=mock_handle)
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect",
                       AsyncMock(return_value=mock_client))
    _make_combined_scan_dir(tmp_path, "WS", "s1", bb_phase="pending",
                            status="running")
    scan_key = ("WS", "s1")
    # 模拟编排 task 在跑（start 时登记的 fire-and-forget task）
    orch = asyncio.ensure_future(asyncio.sleep(100))
    mgr._orchestrator_tasks[scan_key] = orch

    result = await mgr.cancel("WS", "s1")

    assert result == {"cancelled": "s1"}
    # 白盒 workflow_id（resumeAttempts=0 → {ws}-{scan_id}）
    mock_client.get_workflow_handle.assert_called_with("WS-s1")
    mock_handle.cancel.assert_awaited_once()
    # 编排 task 取消 + 出栈
    assert scan_key not in mgr._orchestrator_tasks
    assert orch.cancelled()


# ── cancel: bb_phase=running + rerun=0 → 黑盒 -bb ───────────────────────────

@pytest.mark.asyncio
async def test_cancel_combined_running_rerun0_terminates_bb(tmp_path, monkeypatch):
    """bb_phase=running + 0 → cancel {ws}-{scan_id}-bb。"""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    mock_handle = AsyncMock()
    mock_client = AsyncMock()
    mock_client.get_workflow_handle = MagicMock(return_value=mock_handle)
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect",
                       AsyncMock(return_value=mock_client))
    _make_combined_scan_dir(tmp_path, "WS", "s1", bb_phase="running",
                            bb_rerun_attempts=0, status="running")

    result = await mgr.cancel("WS", "s1")

    assert result == {"cancelled": "s1"}
    mock_client.get_workflow_handle.assert_called_with("WS-s1-bb")
    mock_handle.cancel.assert_awaited_once()


# ── cancel: bb_phase=running + rerun=2 → 黑盒 -bb-rerun-2 ────────────────────

@pytest.mark.asyncio
async def test_cancel_combined_running_rerun2_terminates_bb_rerun2(
        tmp_path, monkeypatch):
    """bb_phase=running + 2 → cancel {ws}-{scan_id}-bb-rerun-2。"""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    mock_handle = AsyncMock()
    mock_client = AsyncMock()
    mock_client.get_workflow_handle = MagicMock(return_value=mock_handle)
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect",
                       AsyncMock(return_value=mock_client))
    _make_combined_scan_dir(tmp_path, "WS", "s1", bb_phase="running",
                            bb_rerun_attempts=2, status="running")

    result = await mgr.cancel("WS", "s1")

    assert result == {"cancelled": "s1"}
    mock_client.get_workflow_handle.assert_called_with("WS-s1-bb-rerun-2")
    mock_handle.cancel.assert_awaited_once()


# ── cancel: 标终态 cancelled（scan_end + session.status）─────────────────────

@pytest.mark.asyncio
async def test_cancel_combined_marks_cancelled_terminal_state(tmp_path, monkeypatch):
    """cancel 后 session.status=cancelled + events 有 scan_end（终态优先，Delete 立即可用）。"""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    mock_handle = AsyncMock()
    mock_client = AsyncMock()
    mock_client.get_workflow_handle = MagicMock(return_value=mock_handle)
    monkeypatch.setattr("supernova_web.components.scan_manager.Client.connect",
                       AsyncMock(return_value=mock_client))
    scan_dir = _make_combined_scan_dir(tmp_path, "WS", "s1", bb_phase="running",
                                       bb_rerun_attempts=0, status="running")

    await mgr.cancel("WS", "s1")

    sess = json.loads((scan_dir / "session.json").read_text())
    assert sess["status"] == "cancelled"
    assert sess.get("completed_at") is not None
    event_text = (scan_dir / "events.ndjson").read_text()
    assert '"scan_end"' in event_text and '"cancelled"' in event_text


# ── 零回归守卫：非组合 resume/cancel 不走 combined 分支 ──────────────────────

@pytest.mark.asyncio
async def test_resume_non_combined_zero_regression(tmp_path, monkeypatch):
    """非组合 scan（combined 缺省）→ resume 走既有白盒路径，不起编排 task。"""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    _patch_temporal_ok(monkeypatch, mgr)
    mock_client = _patch_client(monkeypatch)
    # 非 combined scan（无 combined 字段）
    scan_dir = Path(tmp_path) / "WS" / "scans" / "s1"
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "session.json").write_text(json.dumps({
        "status": "interrupted", "scan_type": "whitebox", "created_at": time.time(),
        "web_url": "http://e", "repo_path": "/code/x"}))

    with patch.object(mgr, "_watch", new=AsyncMock()):
        await mgr.resume("WS", "s1")

    # 既有路径：白盒 -resume-1（无编排 task）
    call = mock_client.start_workflow.call_args
    assert call.kwargs["id"] == "WS-s1-resume-1"
    assert ("WS", "s1") not in mgr._orchestrator_tasks


@pytest.mark.asyncio
async def test_cancel_non_combined_zero_regression(tmp_path, monkeypatch):
    """非组合 scan → cancel 走既有 ① 轨（_handles 里的 handle.cancel），不连 Temporal re-attach。"""
    mgr = ScanManager(tmp_path, tmp_path / "r", None)
    scan_dir = Path(tmp_path) / "WS" / "scans" / "s1"
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "session.json").write_text(json.dumps({
        "status": "running", "scan_type": "whitebox", "created_at": time.time(),
        "web_url": "", "repo_path": ""}))
    mock_handle = AsyncMock()
    mgr._handles[("WS", "s1")] = mock_handle  # 既有 ① 轨：handle 在 _handles

    result = await mgr.cancel("WS", "s1")

    assert result == {"cancelled": "s1"}
    mock_handle.cancel.assert_awaited_once()  # ① 轨 handle.cancel
