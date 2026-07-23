"""回归 2026-07-23 hr_1784788700: worker 路径 heartbeat 必须被写出, 否则 web 判活
(scan_liveness 看 <ws>/heartbeat mtime) 在 120s 提交宽限后误判 interrupted, 而 temporal
workflow 仍 Running、worker 实际在正常跑.

根因: workflows.py 曾用 background activity(start_activity / asyncio.create_task 包的
run_heartbeat) 写 heartbeat. worker max_concurrent_workflow_tasks=1(AuditSession 进程全局
单例所致, runner.py wb_worker) 下, background fire-and-forget activity 被 worker poll 到
task(server pendingActivities.state=STARTED, 记了 worker identity) 却从不 dispatch handler
→ heartbeat 永不写. 与 create_task / start_activity 无关——任何 background activity 都中招.

治本(方案A): heartbeat 不走 background activity, 改由 setup_display(主线 await activity,
真机确认执行)启动 HeartbeatManager daemon 线程; finalize_summary 停止(daemon 终态自停兜底).

测试范式(对齐 test_workflow_migration 既有可靠范式): 不用 start_workflow + 轮询 heartbeat 文件
——并行/background activity 的 poll 时序在 temporal local env 不可靠(既有测试明注, 实测复跑
1.6s~>45s 都可能, setup_display 在部分 run 里根本不被 dispatch). 改用 execute_workflow 阻塞到
workflow 在首个未注册的主体 activity(log_phase_start)失败, mock 的 setup_display 复刻真实末尾
调用 start_heartbeat 写出文件. 主线 await activity 必先于 workflow 失败执行完 → 断言确定性.
"""
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from supernova_core.runtime.heartbeat import start_heartbeat, stop_heartbeat
from supernova_whitebox.pipeline.workflows import WhiteboxScanWorkflow
from supernova_whitebox.pipeline.shared import PipelineInput


@pytest.mark.asyncio
async def test_start_stop_heartbeat_api(tmp_path):
    """unit: start_heartbeat 写出首个 heartbeat, stop_heartbeat 停 daemon."""
    hb = tmp_path / "heartbeat"
    await start_heartbeat(tmp_path)
    written = hb.exists()
    await stop_heartbeat()
    assert written, "start_heartbeat 未写出 heartbeat 文件"


@pytest.mark.asyncio
async def test_worker_path_heartbeat_started_via_setup_display_under_max_concurrent(tmp_path):
    """回归 hr_1784788700: max_concurrent_workflow_tasks=1 下 heartbeat 由 setup_display
    (主线 await activity)启动写出, 不依赖 background activity(后者在该并发下不被 dispatch).

    范式同 test_workflow_migration.test_worker_path_invokes_setup_display_and_heartbeat:
    仅注册 setup_display mock(复刻真实末尾的 start_heartbeat 调用), workflow 在其后首个未注册
    主体 activity(log_phase_start)处 ApplicationError 失败. 主线 await activity 必先于失败
    执行完, 故 setup_display 已 dispatch 且写出 heartbeat. 确定性, 不依赖 background poll 时序.
    """
    inp = PipelineInput(
        repo_path=str(tmp_path),
        workspace_name="ws-hb",
        event_file=str(tmp_path / "events.ndjson"),
        enable_llm_track=False,
    )
    heartbeat_file = tmp_path / "heartbeat"
    started: list = []

    @activity.defn
    async def setup_display(input):
        # 复刻真实 setup_display 末尾(activities.py: setup_display → start_heartbeat(ws_path)):
        # 主线 dispatch 下 heartbeat 被 daemon 线程同步写出首个文件.
        await start_heartbeat(Path(input.workspace_path))
        started.append(input.workspace_path)

    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-hb",
            workflows=[WhiteboxScanWorkflow],
            activities=[setup_display],  # 仅注册 setup_display; workflow 在其后首个未注册 activity 失败
            max_concurrent_workflow_tasks=1,
        ):
            with pytest.raises(Exception):
                await env.client.execute_workflow(
                    WhiteboxScanWorkflow.run, inp,
                    id="w-hb", task_queue="tq-hb",
                )
    try:
        assert started, (
            "setup_display 未在 max_concurrent_workflow_tasks=1 下被 dispatch — "
            "主线 await activity 应被可靠 dispatch(background activity 才不被 dispatch).")
        assert heartbeat_file.exists(), (
            "setup_display 已执行但 heartbeat 未写出 — start_heartbeat 应同步写首个文件.")
    finally:
        # 清理进程级 daemon(避免泄漏到同进程后续测试).
        await stop_heartbeat()
