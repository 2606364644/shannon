"""组合扫描黑盒续跑（T8 / spec §8/§11.3）：rerun_blackbox → 新建下一个版本化 run。

核心契约（版本化多 run 模型，取代旧 -bb-rerun-N / bb_rerun_attempts）：
- 续跑 = 新建下一个 run（run-K+1），workflow_id suffix ``-bb-{K+1}``；旧 run 保留可对比。
- 前置：白盒产物完好；latest run 状态为 failed/skipped（或尚无 run）。
- new_auth 可选：换认证 → _add_blackbox_run 内重 dump scan-config.yaml + 预验证。
- 预验证 fail → 新 run 标 failed（_mark_run）+ 不起黑盒。
- scan_end 不变量：rerun 经 _rerun_orchestrator 的 _ensure_scan_end 幂等收尾。
"""
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supernova_core.session import SessionManager
from supernova_web.components.scan_manager import ScanManager
from supernova_web.components.scan_store import ScanStore
from supernova_web.models import ScanRequest


@pytest.fixture
def mgr(tmp_path):
    return ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())


def _make_combined(workspaces_dir, ws, scan_id, with_deliverables=True,
                   bb_url="http://target.example/"):
    """建组合白盒任务根：workspaces/<ws>/scans/<scan_id>/session.json + deliverables/whitebox/。

    任务级 session 仅记 combined=True（run 状态下沉到 run 级 session，由 create_blackbox_run
    写 bb_runs[]）。返回 scan_dir。
    """
    scan_dir = Path(workspaces_dir) / ws / "scans" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    sess = {
        "status": "completed", "scan_type": "whitebox", "created_at": time.time(),
        "web_url": bb_url, "repo_path": "/code/x",
        "combined": True, "bb_url": bb_url, "bb_auth_ref": {"profile_id": None},
    }
    (scan_dir / "session.json").write_text(json.dumps(sess))
    if with_deliverables:
        wb = scan_dir / "deliverables" / "whitebox"
        wb.mkdir(parents=True, exist_ok=True)
        (wb / "recon_deliverable.md").write_text("recon")
        (wb / "injection_exploitation_queue.json").write_text(
            '{"vulnerabilities":[{"id":1}]}')
    return scan_dir


def _new_auth_req(**kw) -> ScanRequest:
    base = {
        "type": "whitebox", "url": "http://target.example/", "workspace": "ws-a",
        "authentication": {
            "login_type": "form", "login_url": "http://target.example/login",
            "credentials": {"username": "new", "password": "new-secret"},
        },
    }
    base.update(kw)
    return ScanRequest(**base)


async def _drain_bg_tasks(mgr):
    for t in list(mgr._orchestrator_tasks.values()):
        if not t.done():
            await t


# ── 续跑 = 下一个 run（run-K+1，suffix -bb-{K+1}）────────────────────────────

async def test_rerun_creates_next_run_after_failed(mgr, tmp_path):
    """latest run failed → rerun 建 run-2（suffix -bb-2），run-1 保留。"""
    ws, scan_id = "ws-a", "s1"
    scan_dir = _make_combined(tmp_path, ws, scan_id)
    store = ScanStore(tmp_path); mgr._store = store
    store.create_blackbox_run(ws, scan_id)  # run-1
    store.update_blackbox_run(ws, scan_id, "run-1", status="failed", phase="failed")
    bb_handle = MagicMock(); bb_handle.result = AsyncMock(return_value=None)
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=True)), \
         patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)) as sb, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch.object(mgr, "_write_scan_end", new=AsyncMock()), \
         patch.object(mgr, "_mark_run", new=AsyncMock()):
        run_id = await mgr.rerun_blackbox(ws, scan_id)
        await _drain_bg_tasks(mgr)
    assert run_id == "run-2"
    assert sb.call_args.kwargs.get("workflow_id_suffix") == "-bb-2"
    runs = store.list_blackbox_runs(ws, scan_id)
    assert [r["run_id"] for r in runs] == ["run-1", "run-2"]


async def test_rerun_creates_run1_when_no_runs(mgr, tmp_path):
    """无 run（仅白盒任务）→ rerun 视为首个 run-1（suffix -bb-1）。"""
    ws, scan_id = "ws-a", "s1"
    _make_combined(tmp_path, ws, scan_id)
    store = ScanStore(tmp_path); mgr._store = store
    bb_handle = MagicMock(); bb_handle.result = AsyncMock(return_value=None)
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=True)), \
         patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)) as sb, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch.object(mgr, "_write_scan_end", new=AsyncMock()), \
         patch.object(mgr, "_mark_run", new=AsyncMock()):
        run_id = await mgr.rerun_blackbox(ws, scan_id)
        await _drain_bg_tasks(mgr)
    assert run_id == "run-1"
    assert sb.call_args.kwargs.get("workflow_id_suffix") == "-bb-1"


# ── 换认证：重 dump scan-config.yaml ─────────────────────────────────────────

async def test_rerun_with_new_auth_redumps_scan_config(mgr, tmp_path):
    """传 new_auth → scan-config.yaml 被重写（含新认证）。"""
    ws, scan_id = "ws-a", "s1"
    scan_dir = _make_combined(tmp_path, ws, scan_id)
    store = ScanStore(tmp_path); mgr._store = store
    store.create_blackbox_run(ws, scan_id)
    store.update_blackbox_run(ws, scan_id, "run-1", status="failed", phase="failed")
    bb_handle = MagicMock(); bb_handle.result = AsyncMock(return_value=None)
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=True)) as rc, \
         patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)), \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch.object(mgr, "_write_scan_end", new=AsyncMock()), \
         patch.object(mgr, "_mark_run", new=AsyncMock()):
        await mgr.rerun_blackbox(ws, scan_id, new_auth=_new_auth_req())
        await _drain_bg_tasks(mgr)
        rc.assert_awaited()  # 预验证新认证
    cfg = scan_dir / "scan-config.yaml"
    assert cfg.exists(), "new_auth 应触发 scan-config.yaml 重 dump"
    body = cfg.read_text("utf-8")
    assert "new" in body and "new-secret" in body


# ── 预验证 fail → 新 run 标 failed ────────────────────────────────────────────

async def test_rerun_precheck_fail_marks_run_failed(mgr, tmp_path):
    """_run_precheck False → 新 run（run-2）标 failed（_mark_run），不起黑盒。
    precheck 在 _add_run_kickoff 后台 task 内跑（2026-08-17 异步化），drain 后断言。"""
    ws, scan_id = "ws-a", "s1"
    _make_combined(tmp_path, ws, scan_id)
    store = ScanStore(tmp_path); mgr._store = store
    store.create_blackbox_run(ws, scan_id)
    store.update_blackbox_run(ws, scan_id, "run-1", status="failed", phase="failed")
    (store.get_scan_dir(ws, scan_id) / "scan-config.yaml").write_text("url: http://t")
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=False)), \
         patch.object(mgr, "_submit_blackbox", new=AsyncMock()) as sb, \
         patch.object(mgr, "_mark_run", new=AsyncMock()) as mr, \
         patch.object(mgr, "_ensure_scan_end", new=AsyncMock()):
        run_id = await mgr.rerun_blackbox(ws, scan_id)
        await _drain_bg_tasks(mgr)
    assert run_id == "run-2"
    sb.assert_not_awaited()
    mr.assert_awaited_with(
        store.get_scan_dir(ws, scan_id), "run-2", "failed",
        reason="auth_failed", status="failed",
        extra={"bb_failure_point": None, "bb_failure_detail": None})


# ── 守卫：latest 非 failed/skipped 拒续跑（零回归）────────────────────────────

async def test_rerun_rejects_when_latest_completed(mgr, tmp_path):
    """latest run completed（非 failed/skipped）→ ValueError（不能续跑成功 run）。"""
    ws, scan_id = "ws-a", "s1"
    _make_combined(tmp_path, ws, scan_id)
    store = ScanStore(tmp_path); mgr._store = store
    store.create_blackbox_run(ws, scan_id)
    store.update_blackbox_run(ws, scan_id, "run-1", status="completed", phase="completed")
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock()) as sb:
        with pytest.raises(ValueError, match="latest"):
            await mgr.rerun_blackbox(ws, scan_id)
        sb.assert_not_awaited()


async def test_rerun_rejects_when_deliverables_missing(mgr, tmp_path):
    """白盒产物缺失 → ValueError。"""
    ws, scan_id = "ws-a", "s1"
    _make_combined(tmp_path, ws, scan_id, with_deliverables=False)
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock()) as sb:
        with pytest.raises(ValueError, match="产物"):
            await mgr.rerun_blackbox(ws, scan_id)
        sb.assert_not_awaited()


async def test_rerun_rejects_unknown_scan(mgr, tmp_path):
    """scan 不存在 → ValueError。"""
    with pytest.raises(ValueError, match="不存在"):
        await mgr.rerun_blackbox("ws-a", "nope")


# ── _run_blackbox_phase suffix 透传（run 化后默认 -bb-1）─────────────────────

async def test_run_blackbox_phase_default_suffix_is_bb_1(mgr, tmp_path):
    """_run_blackbox_phase 默认 workflow_id_suffix='-bb-1'（run-K，零回归口径）。"""
    ws, scan_id = "ws-a", "s1"
    scan_dir = _make_combined(tmp_path, ws, scan_id)
    store = ScanStore(tmp_path); mgr._store = store
    store.create_blackbox_run(ws, scan_id)  # run-1
    bb_handle = MagicMock(); bb_handle.result = AsyncMock(return_value=None)
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)) as sb, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch.object(mgr, "_mark_run", new=AsyncMock()):
        await mgr._run_blackbox_phase(scan_dir, ws, scan_id, {"profile_id": None}, "run-1")
        assert sb.call_args.kwargs.get("workflow_id_suffix") == "-bb-1"


async def test_run_blackbox_phase_custom_suffix_propagated(mgr, tmp_path):
    """传 workflow_id_suffix='-bb-5' → _submit_blackbox 拿到 '-bb-5'。"""
    ws, scan_id = "ws-a", "s1"
    scan_dir = _make_combined(tmp_path, ws, scan_id)
    (scan_dir / "blackbox-runs" / "run-5").mkdir(parents=True)
    bb_handle = MagicMock(); bb_handle.result = AsyncMock(return_value=None)
    with patch.object(mgr, "_submit_blackbox", new=AsyncMock(return_value=bb_handle)) as sb, \
         patch.object(mgr, "_generate_combined_report", new=AsyncMock()), \
         patch.object(mgr, "_mark_run", new=AsyncMock()):
        await mgr._run_blackbox_phase(
            scan_dir, ws, scan_id, {"profile_id": None}, "run-5",
            workflow_id_suffix="-bb-5")
        assert sb.call_args.kwargs.get("workflow_id_suffix") == "-bb-5"
