"""run_worker：连接 temporal，起两个常驻 Worker（白盒/黑盒固定 queue），注册对应 workflow+activities。"""
import os
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest

from supernova_core.services.temporal_infra import (
    WEB_TASK_QUEUE_WHITEBOX,
    WEB_TASK_QUEUE_BLACKBOX,
)
from supernova_whitebox.pipeline.workflows import WhiteboxScanWorkflow
from supernova_blackbox.pipeline.workflows import BlackboxScanWorkflow


@pytest.mark.asyncio
async def test_run_worker_connects_and_registers_two_workers(monkeypatch):
    """run_worker 连 temporal + 起两个 Worker（白盒/黑盒固定 queue）+ 并行 run。"""
    from supernova_worker.runner import run_worker

    monkeypatch.delenv("SUPERNOVA_WORKER_MAX_CONCURRENT_WF", raising=False)
    mock_client = AsyncMock()

    wb_worker = MagicMock()
    wb_worker.run = AsyncMock(return_value=None)
    bb_worker = MagicMock()
    bb_worker.run = AsyncMock(return_value=None)

    with (
        patch("supernova_worker.runner.Client.connect",
              AsyncMock(return_value=mock_client)) as mock_connect,
        patch("supernova_worker.runner.Worker",
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

    # P3c 阶段 3：contextvar 化（AuditSession/LogBus/heartbeat 按 workflow_id 隔离）后
    # 并发不再串台。max_concurrent 读 SUPERNOVA_WORKER_MAX_CONCURRENT_WF（默认 4）。
    assert bb_call.kwargs["max_concurrent_workflow_tasks"] == 4

    # 两个 worker 都 run（并行 gather）
    wb_worker.run.assert_awaited_once()
    bb_worker.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_worker_propagates_connect_failure():
    """temporal 连不上时 run_worker 抛错（fail-fast，不静默吞）。"""
    from supernova_worker.runner import run_worker

    with patch("supernova_worker.runner.Client.connect",
               AsyncMock(side_effect=RuntimeError("temporal down"))):
        with pytest.raises(RuntimeError, match="temporal down"):
            await run_worker("bad:7233")


@pytest.mark.asyncio
async def test_run_worker_whitebox_concurrent_and_migration_activities(monkeypatch):
    """白盒 Worker: 注册 setup_display/finalize_summary（display 生命周期 activity）。

    P3c 阶段 3：contextvar 化后 max_concurrent 放开（默认 4）。run_heartbeat 非注册
    activity（heartbeat 由 setup_display 内 HeartbeatManager 启动），旧断言过时已删。
    """
    from supernova_worker.runner import run_worker

    monkeypatch.delenv("SUPERNOVA_WORKER_MAX_CONCURRENT_WF", raising=False)
    mock_client = AsyncMock()
    wb_worker = MagicMock()
    wb_worker.run = AsyncMock()
    bb_worker = MagicMock()
    bb_worker.run = AsyncMock()

    with patch("supernova_worker.runner.Client.connect",
               AsyncMock(return_value=mock_client)), \
         patch("supernova_worker.runner.Worker",
               side_effect=[wb_worker, bb_worker]) as mw:
        await run_worker("temporal:7233")

    wb_call = mw.call_args_list[0]
    assert wb_call.kwargs["max_concurrent_workflow_tasks"] == 4, \
        "白盒 Worker 并发默认 4(contextvar 化后放开)"
    activity_names = {getattr(a, "__name__", a) for a in wb_call.kwargs["activities"]}
    assert "setup_display" in activity_names, "白盒 Worker 须注册 setup_display"
    assert "finalize_summary" in activity_names, "白盒 Worker 须注册 finalize_summary"
    # auth GitNexus 轨已删(plan zazzy-roaming-shamir, 2026-07-14):
    # run_auth_config_scan/run_auth_gitnexus_judge 不再存在, 不注册.


@pytest.mark.asyncio
async def test_worker_max_concurrent_reads_env(monkeypatch):
    """max_concurrent_workflow_tasks 读 SUPERNOVA_WORKER_MAX_CONCURRENT_WF（默认 4，env 可配）。"""
    from supernova_worker.runner import run_worker

    monkeypatch.setenv("SUPERNOVA_WORKER_MAX_CONCURRENT_WF", "8")
    mock_client = AsyncMock()
    wb_worker = MagicMock()
    wb_worker.run = AsyncMock()
    bb_worker = MagicMock()
    bb_worker.run = AsyncMock()
    with patch("supernova_worker.runner.Client.connect",
               AsyncMock(return_value=mock_client)), \
         patch("supernova_worker.runner.Worker",
               side_effect=[wb_worker, bb_worker]) as mw:
        await run_worker("temporal:7233")
    assert mw.call_args_list[0].kwargs["max_concurrent_workflow_tasks"] == 8
    assert mw.call_args_list[1].kwargs["max_concurrent_workflow_tasks"] == 8


def test_main_loads_profile_env_before_starting_worker():
    """main() 启动时先 load_env()——锁住「worker 入口必须加载 profile 凭证」不变量。

    根因(2026-07-15 真机 trip_1784107863): worker runner.main() 漏调 load_env(),
    profile 的 SUPERNOVA_AI_PROVIDER / SUPERNOVA_OPENAI_* 不进进程环境 → 引擎回落
    anthropic_api(claude CLI)→ deepseek-openai profile 无 ANTHROPIC 凭证 →
    claude CLI 子进程 "Not logged in · Please run /login" → pre-recon 失败 →
    扫描卡死、WEB 各页全空。对齐 CLI 入口(whitebox/blackbox/combined main.py
    均首行 load_env())。
    """
    from supernova_worker import runner

    # 同一 parent Mock 记录调用顺序，验证 load_env 先于 asyncio.run。
    # patch run_worker 避免真起 temporal 连接 + 不留未 await coroutine。
    parent = MagicMock()
    with patch.object(runner, "load_env", parent.load_env), \
         patch.object(runner, "asyncio", parent.asyncio), \
         patch.object(runner, "run_worker", return_value="worker-coroutine"), \
         patch.dict(os.environ, {"SUPERNOVA_TEMPORAL_HOST": "th", "SUPERNOVA_TEMPORAL_PORT": "tp"}):
        runner.main()

    # load_env 必须在起 worker 之前调用（profile 凭证先于 provider 配置加载）
    parent.assert_has_calls([call.load_env(), call.asyncio.run(ANY)])
    parent.load_env.assert_called_once()
    parent.asyncio.run.assert_called_once()
