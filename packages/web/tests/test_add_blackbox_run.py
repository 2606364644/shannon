"""T6: _add_blackbox_run — 给已有白盒任务加一个嵌套黑盒 run（spec §6/§7.1 #8）。

手动加黑盒入口（API POST /blackbox-runs + rerun 复用）：预验证 → create_blackbox_run
→ fire _rerun_orchestrator(run_id, -bb-{K})。预验证 fail → run 标 failed。
"""
import asyncio
from unittest.mock import AsyncMock, patch


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
        scan_dir, "run-1", "failed", reason="auth_failed", status="failed")


async def test_add_blackbox_run_second_run_monotonic(tmp_path):
    from supernova_web.components.scan_store import ScanStore
    mgr = _mgr(tmp_path)
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = store.create_scan("ws", "http://t", "/code/x")
    _ready_whitebox(scan_dir)
    store.create_blackbox_run("ws", wb_id)  # 已有 run-1
    with patch.object(mgr, "_run_precheck", new=AsyncMock(return_value=True)), \
         patch.object(mgr, "_rerun_orchestrator", new=AsyncMock()):
        run_id = await mgr._add_blackbox_run("ws", wb_id)
        await asyncio.sleep(0)
    assert run_id == "run-2"  # 序号 per-task 单调递增
