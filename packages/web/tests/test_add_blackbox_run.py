"""T6: _add_blackbox_run — 给已有白盒任务加一个嵌套黑盒 run（spec §6/§7.1 #8）。

手动加黑盒入口（API POST /blackbox-runs + rerun 复用）：预验证 → create_blackbox_run
→ fire _rerun_orchestrator(run_id, -bb-{K})。预验证 fail → run 标 failed。
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest


def _mgr(tmp_path):
    from supernova_web.components.scan_manager import ScanManager
    return ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())


def _ready_whitebox(scan_dir):
    """让 _whitebox_deliverables_ready 通过：recon + 非空 queue。"""
    wb = scan_dir / "deliverables" / "whitebox"
    wb.mkdir(parents=True, exist_ok=True)
    (wb / "recon_deliverable.md").write_text("recon")
    (wb / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities":[{"id":1}]}')


async def test_add_blackbox_run_creates_run_and_fires_orchestrator(tmp_path):
    from supernova_web.components.scan_store import ScanStore
    mgr = _mgr(tmp_path)
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    _ready_whitebox(scan_dir)
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=True)), \
         patch.object(mgr, "_rerun_orchestrator", new=AsyncMock()) as orch:
        run_id = await mgr._add_blackbox_run("ws", wb_id)
        await asyncio.sleep(0)  # 让 fire-and-forget orchestrator task 跑一轮
    assert run_id == "run-1"
    assert (scan_dir / "blackbox-runs" / "run-1" / "session.json").exists()
    orch.assert_awaited()  # 编排被 fire


async def test_add_blackbox_run_precheck_fail_marks_run_failed(tmp_path):
    from supernova_web.components.scan_store import ScanStore
    mgr = _mgr(tmp_path)
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    _ready_whitebox(scan_dir)
    (scan_dir / "scan-config.yaml").write_text("url: http://t")  # 有认证 → 走 precheck
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=False)), \
         patch.object(mgr, "_mark_run", new=AsyncMock()) as mr, \
         patch.object(mgr, "_ensure_scan_end", new=AsyncMock()):
        run_id = await mgr._add_blackbox_run("ws", wb_id)
    assert run_id == "run-1"
    mr.assert_awaited_with(
        scan_dir, "run-1", "failed", reason="auth_failed", status="failed",
        extra={"bb_failure_point": None, "bb_failure_detail": None})


async def test_add_blackbox_run_second_run_monotonic(tmp_path):
    from supernova_web.components.scan_store import ScanStore
    mgr = _mgr(tmp_path)
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    _ready_whitebox(scan_dir)
    store.create_blackbox_run("ws", wb_id)  # 已有 run-1
    # run-1 标终态（在跑守卫放行终态 latest，对齐 rerun_blackbox 门）
    store.update_blackbox_run("ws", wb_id, "run-1", status="completed", phase="completed")
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=True)), \
         patch.object(mgr, "_rerun_orchestrator", new=AsyncMock()):
        run_id = await mgr._add_blackbox_run("ws", wb_id)
        await asyncio.sleep(0)
    assert run_id == "run-2"  # 序号 per-task 单调递增


async def test_add_blackbox_run_rejects_missing_target_url(tmp_path):
    """纯白盒任务（bb_url/web_url 皆空）→ ValueError（端点 422）：黑盒无目标不打空跑。
    黑盒 workflow 对空 web_url 不 fail-fast（preflight 仅在有 URL 时校验可达性，exploit
    agent 拿空 web_url 照跑一轮 LLM），须在入口拦。"""
    from supernova_web.components.scan_store import ScanStore
    mgr = _mgr(tmp_path)
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "", "/code/x")  # 纯白盒：web_url 空
    _ready_whitebox(scan_dir)
    with pytest.raises(ValueError, match="目标 URL"):
        await mgr._add_blackbox_run("ws", wb_id)


async def test_add_blackbox_run_rejects_active_latest_run(tmp_path):
    """latest run 非终态（pending/running）→ 拒绝叠加：防并发 run 同打一目标 +
    _orchestrator_tasks[key] 被覆盖（cancel 只能取消 latest）。对齐 rerun_blackbox 状态门。"""
    from supernova_web.components.scan_store import ScanStore
    mgr = _mgr(tmp_path)
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    _ready_whitebox(scan_dir)
    store.create_blackbox_run("ws", wb_id)  # run-1（status=pending，非终态）
    with pytest.raises(ValueError, match="仍在进行"):
        await mgr._add_blackbox_run("ws", wb_id)
