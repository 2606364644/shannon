"""run_worker：连接 temporal，起两个常驻 Worker（白盒/黑盒固定 queue），注册对应 workflow+activities。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.services.temporal_infra import (
    WEB_TASK_QUEUE_WHITEBOX,
    WEB_TASK_QUEUE_BLACKBOX,
)
from shannon_whitebox.pipeline.workflows import WhiteboxScanWorkflow
from shannon_blackbox.pipeline.workflows import BlackboxScanWorkflow


@pytest.mark.asyncio
async def test_run_worker_connects_and_registers_two_workers():
    """run_worker 连 temporal + 起两个 Worker（白盒/黑盒固定 queue）+ 并行 run。"""
    from shannon_worker.runner import run_worker

    mock_client = AsyncMock()

    wb_worker = MagicMock()
    wb_worker.run = AsyncMock(return_value=None)
    bb_worker = MagicMock()
    bb_worker.run = AsyncMock(return_value=None)

    with (
        patch("shannon_worker.runner.Client.connect",
              AsyncMock(return_value=mock_client)) as mock_connect,
        patch("shannon_worker.runner.Worker",
              side_effect=[wb_worker, bb_worker]) as mock_worker_cls,
    ):
        await run_worker("temporal:7233")

    # 连接 temporal
    mock_connect.assert_awaited_once_with("temporal:7233")

    # 两个 Worker 创建，task_queue + workflows 正确
    assert mock_worker_cls.call_count == 2
    wb_call, bb_call = mock_worker_cls.call_args_list
    assert wb_call.kwargs["client"] is mock_client
    assert wb_call.kwargs["task_queue"] == WEB_TASK_QUEUE_WHITEBOX
    assert WhiteboxScanWorkflow in wb_call.kwargs["workflows"]
    assert len(wb_call.kwargs["activities"]) >= 20  # 白盒 ~25 activities
    assert bb_call.kwargs["client"] is mock_client
    assert bb_call.kwargs["task_queue"] == WEB_TASK_QUEUE_BLACKBOX
    assert BlackboxScanWorkflow in bb_call.kwargs["workflows"]
    assert len(bb_call.kwargs["activities"]) >= 10  # 黑盒 ~16 activities

    # 两个 worker 都 run（并行 gather）
    wb_worker.run.assert_awaited_once()
    bb_worker.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_worker_propagates_connect_failure():
    """temporal 连不上时 run_worker 抛错（fail-fast，不静默吞）。"""
    from shannon_worker.runner import run_worker

    with patch("shannon_worker.runner.Client.connect",
               AsyncMock(side_effect=RuntimeError("temporal down"))):
        with pytest.raises(RuntimeError, match="temporal down"):
            await run_worker("bad:7233")


@pytest.mark.asyncio
async def test_run_worker_whitebox_concurrency_one_and_migration_activities():
    """白盒 Worker: max_concurrent_workflow_tasks=1(AuditSession 进程全局单例约束) + 注册迁移 activity.

    AuditSession 是进程全局 _current(session_registry.py), events.ndjson 经它写.
    worker 容器并发多白盒扫描会冲突 → 白盒 Worker 并发=1. 解锁需把 AuditSession 改 contextvar(留 follow-up).
    """
    from shannon_worker.runner import run_worker

    mock_client = AsyncMock()
    wb_worker = MagicMock()
    wb_worker.run = AsyncMock()
    bb_worker = MagicMock()
    bb_worker.run = AsyncMock()

    with patch("shannon_worker.runner.Client.connect",
               AsyncMock(return_value=mock_client)), \
         patch("shannon_worker.runner.Worker",
               side_effect=[wb_worker, bb_worker]) as mw:
        await run_worker("temporal:7233")

    wb_call = mw.call_args_list[0]
    assert wb_call.kwargs["max_concurrent_workflow_tasks"] == 1, \
        "白盒 Worker 并发=1(AuditSession 进程全局单例约束)"
    activity_names = {getattr(a, "__name__", a) for a in wb_call.kwargs["activities"]}
    assert "setup_display" in activity_names, "白盒 Worker 须注册 setup_display"
    assert "run_heartbeat" in activity_names, "白盒 Worker 须注册 run_heartbeat"
    assert "finalize_summary" in activity_names, "白盒 Worker 须注册 finalize_summary"
    # auth GitNexus 轨已删(plan zazzy-roaming-shamir, 2026-07-14):
    # run_auth_config_scan/run_auth_gitnexus_judge 不再存在, 不注册.
