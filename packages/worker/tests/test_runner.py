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
