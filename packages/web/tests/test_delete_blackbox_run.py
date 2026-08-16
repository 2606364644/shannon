"""删除单个黑盒 run（spec §7.1 #4，DELETE /blackbox-runs/{run_id} 的 manager 包装）。

镜像 test_add_blackbox_run：真实 ScanStore + tmpdir；delete_blackbox_run 只依赖 store +
读 run 级 session status 判活跃态（不碰 orchestrator/precheck）。
"""
import pytest

from supernova_web.components.scan_manager import ScanManager, ScanRunning
from supernova_web.components.scan_store import ScanStore


def _mgr(tmp_path) -> ScanManager:
    return ScanManager(workspaces_dir=tmp_path, repos_dir=tmp_path, config_store=object())


def _make_run(store: ScanStore, ws="ws"):
    wb_id, scan_dir = store.create_scan(ws, "http://t", "/code/x")
    store.create_blackbox_run(ws, wb_id)  # run-1
    return wb_id, scan_dir


async def test_delete_blackbox_run_terminal_deletes(tmp_path):
    mgr = _mgr(tmp_path)
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = _make_run(store)
    store.update_blackbox_run("ws", wb_id, "run-1", status="completed")  # 终态

    result = await mgr.delete_blackbox_run("ws", wb_id, "run-1")

    assert result == {"deleted": "run-1"}
    assert store.get_blackbox_run_dir("ws", wb_id, "run-1") is None  # 目录已删
    assert store.list_blackbox_runs("ws", wb_id) == []  # bb_runs[] 条目移除
    # 删光最后一个 run 后组合标记回滚（combined=False，bb_phase/bb_reason 清空），
    # 不留 combined=True + bb_runs=[] 名存实亡态（旧前端据此渲染展开按钮）。
    from supernova_core.session import SessionManager
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data.get("combined") is False
    assert data.get("bb_phase") is None
    assert data.get("bb_reason") is None


async def test_delete_blackbox_run_running_raises(tmp_path):
    mgr = _mgr(tmp_path)
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = _make_run(store)
    store.update_blackbox_run("ws", wb_id, "run-1", status="running")  # 在跑

    with pytest.raises(ScanRunning):
        await mgr.delete_blackbox_run("ws", wb_id, "run-1")

    # 未删：目录 + bb_runs[] 条目保留
    assert store.get_blackbox_run_dir("ws", wb_id, "run-1") is not None
    assert len(store.list_blackbox_runs("ws", wb_id)) == 1


async def test_delete_blackbox_run_pending_raises(tmp_path):
    """刚创建未跑的 run（status=pending）也拒删——已登记编排，对齐 scan 级 running 口径。"""
    mgr = _mgr(tmp_path)
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = _make_run(store)  # run-1 默认 pending

    with pytest.raises(ScanRunning):
        await mgr.delete_blackbox_run("ws", wb_id, "run-1")


# ── 任务级 delete 的 bb_runs 防御（2026-08-17 根因修）───────────────────────
# run 在跑而任务级停终态（race/legacy 状态）时，delete 只查任务级 running 会整目录删掉
# 在跑 run（workflow 还在写产物）。bb_runs 任一非终态 → ScanRunning（先 cancel 再删）。

async def test_delete_scan_with_active_run_raises(tmp_path):
    """任务级 status=completed（legacy：run 在跑不反映）+ run-1 running → 任务级 delete 拒。"""
    mgr = _mgr(tmp_path)
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = _make_run(store)
    store.update_blackbox_run("ws", wb_id, "run-1", status="running")
    from supernova_core.session import SessionManager
    SessionManager(scan_dir.parent).update_session(
        scan_dir, {"status": "completed", "completed_at": 1750000000.0})

    with pytest.raises(ScanRunning):
        await mgr.delete("ws", wb_id)
    assert scan_dir.exists(), "在跑 run 的任务目录不得删除"


async def test_delete_scan_after_run_cancelled_succeeds(tmp_path):
    """run 标 cancelled（终态）→ 任务级 delete 放行：cancel → delete 链路闭环。"""
    mgr = _mgr(tmp_path)
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = _make_run(store)
    store.update_blackbox_run("ws", wb_id, "run-1", status="cancelled")
    from supernova_core.session import SessionManager
    SessionManager(scan_dir.parent).update_session(
        scan_dir, {"status": "cancelled", "completed_at": 1750000000.0})

    result = await mgr.delete("ws", wb_id)
    assert result == {"deleted": wb_id}
    assert not scan_dir.exists()


async def test_delete_scan_all_runs_terminal_succeeds(tmp_path):
    """全 run 终态（completed）→ 任务级 delete 正常（零回归）。"""
    mgr = _mgr(tmp_path)
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = _make_run(store)
    store.update_blackbox_run("ws", wb_id, "run-1", status="completed")
    from supernova_core.session import SessionManager
    SessionManager(scan_dir.parent).update_session(
        scan_dir, {"status": "completed", "completed_at": 1750000000.0})

    assert await mgr.delete("ws", wb_id) == {"deleted": wb_id}
    assert not scan_dir.exists()


async def test_delete_blackbox_run_missing_returns_none(tmp_path):
    mgr = _mgr(tmp_path)
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = _make_run(store)

    assert await mgr.delete_blackbox_run("ws", wb_id, "run-99") is None


async def test_delete_blackbox_run_keeps_combined_when_runs_remain(tmp_path):
    """删非最后一个 run 时 combined 保持 True、latest 回退到上一个——只在删光才回滚组合标记。"""
    mgr = _mgr(tmp_path)
    store = ScanStore(tmp_path); mgr._store = store
    wb_id, scan_dir = _make_run(store)            # run-1
    store.create_blackbox_run("ws", wb_id)        # run-2
    store.update_blackbox_run("ws", wb_id, "run-2", status="completed")

    result = await mgr.delete_blackbox_run("ws", wb_id, "run-2")

    assert result == {"deleted": "run-2"}
    from supernova_core.session import SessionManager
    data = SessionManager(scan_dir.parent).get_session_data(scan_dir)
    assert data.get("combined") is True          # 仍有 run-1，保持组合态
    assert data.get("latest_bb_run") == "run-1"  # latest 回退
