"""T1 机制裁决（spec 2026-08-28-temporal-native-cancel-design）：真 Temporal server
（WorkflowEnvironment.start_local）验证取消传递机制。

实测钉死的三个机制（驱动修复设计；探针 workflow/activity 在
supernova_core/testing/cancel_probe.py，src 正规包 sandbox import 稳）：

1. workflow cancel 不等 activity 终态：RequestCancelActivityTask 后沙箱 activity
   future 立即抛 ActivityError——探针 workflow（无 except）3s 内终态 CANCELED，
   即使 activity 本体还在跑。
2. cancel 注入可能丢失且不重投：await 点是（shielded）activity future 时，
   CancelledError 注入丢失；业务 except Exception 吞掉 ActivityError 后 workflow
   继续推进——吞后新 activity 即使带 heartbeat 也不保证收到取消（时序相关）。
   （2026-08-28 幽灵扫描事故的机制）
3. heartbeat 是取消传递的确定性通道（官方推荐路径；worker poll 通道的 cancel
   推送时序不稳定，实测有竞态）。

注意：Worker 传 default_heartbeat_throttle_interval=100ms——默认 30s 节流会把
心跳/取消传播拖到 30s+。
"""
import asyncio
import contextlib
from datetime import timedelta

import pytest
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from supernova_core.testing.cancel_probe import (
    _SENTINEL, CancelProbeWorkflow, CancelSwallowWorkflow, probe_activity,
)


async def _wait_activity_started() -> None:
    for _ in range(100):
        if _SENTINEL.get("started"):
            return
        await asyncio.sleep(0.1)
    raise AssertionError("activity 未启动（轮询 10s 超时）")


async def test_heartbeat_activity_cancel_delivered():
    """心跳 activity 收到取消：CancelledError 抛在业务 await 点，workflow Canceled。"""
    _SENTINEL.clear()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-cancel-probe-hb",
            workflows=[CancelProbeWorkflow], activities=[probe_activity],
            default_heartbeat_throttle_interval=timedelta(milliseconds=100),
        ):
            handle = await env.client.start_workflow(
                CancelProbeWorkflow.run, True,
                id="cancel-probe-hb", task_queue="tq-cancel-probe-hb",
            )
            await _wait_activity_started()
            await handle.cancel()
            # workflow 取消时 result() 抛 WorkflowFailureError（cause=CancelledError）
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(handle.result(), timeout=20)
    assert _SENTINEL.get("cancelled"), (
        "取消未送达 activity：heartbeat 通道失效？（触发回退路径评估）"
    )


async def test_workflow_cancel_does_not_wait_activity():
    """workflow cancel 不等 activity 终态：无 except 的 workflow 秒级 Canceled。

    即使 activity 本体不心跳且还在跑（sleep 120s），沙箱 activity future 立即
    抛 ActivityError → workflow 终态 CANCELED。「workflow 终态」与「activity 本体
    存活」是两回事——后者是幽灵扫描烧钱的主体。
    """
    _SENTINEL.clear()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-cancel-probe-nohb",
            workflows=[CancelProbeWorkflow], activities=[probe_activity],
            default_heartbeat_throttle_interval=timedelta(milliseconds=100),
        ):
            handle = await env.client.start_workflow(
                CancelProbeWorkflow.run, False,
                id="cancel-probe-nohb", task_queue="tq-cancel-probe-nohb",
            )
            await _wait_activity_started()
            await handle.cancel()
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(handle.result(), timeout=20)
            desc = await handle.describe()
            with contextlib.suppress(Exception):  # 已终态的 workflow terminate 抛错
                await handle.terminate()  # 清场（worker 关闭时收掉 activity）
    assert desc.status == WorkflowExecutionStatus.CANCELED, (
        f"无 except 的 workflow 应在 cancel 后快速 Canceled，status={desc.status}"
    )


async def test_swallowed_cancel_leaks_running_workflow():
    """钉死 2026-08-28 事故机制：workflow 吞掉取消异常 → workflow 泄漏 Running。

    业务 except Exception 吞掉 ActivityError(cancelled) 后：workflow 继续推进
    （stage=phase2）且终态永不翻转（Running——既非 Canceled 也非完成）。这正是
    whitebox workflows.py:685 non-fatal 吞点让事故扫描多烧 9 分钟的机制；
    白盒/黑盒 workflow 吞点放行取消（修复 0）的必要性由此驱动。
    """
    _SENTINEL.clear()
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-cancel-swallow",
            workflows=[CancelSwallowWorkflow], activities=[probe_activity],
            default_heartbeat_throttle_interval=timedelta(milliseconds=100),
        ):
            handle = await env.client.start_workflow(
                CancelSwallowWorkflow.run,
                id="cancel-swallow", task_queue="tq-cancel-swallow",
            )
            await _wait_activity_started()
            await handle.cancel()
            stage = ""
            for _ in range(150):  # 15s 观察窗：足够吞掉+推进+（若会）重发取消
                await asyncio.sleep(0.1)
                try:
                    stage = await handle.query(CancelSwallowWorkflow.stage)
                except Exception:  # noqa: BLE001 — query 瞬态失败重试
                    pass
            desc = await handle.describe()
            await handle.terminate()  # 清场
    assert stage == "phase2", f"workflow 应已吞取消并推进，stage={stage}"
    # workflow 泄漏 Running——吞掉取消后终态永不翻转
    assert desc.status == WorkflowExecutionStatus.RUNNING, (
        f"吞掉取消的 workflow 应泄漏 Running，status={desc.status}"
    )
