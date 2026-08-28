"""取消传递机制探针（spec 2026-08-28-temporal-native-cancel-design T1）。

最小 CancelProbeWorkflow + probe_activity：验证「activity 周期调
activity.heartbeat() 但 execute_activity 不设 heartbeat_timeout」时，workflow
handle.cancel() 的取消能否经心跳通道送达（SDK cancel 整个 activity asyncio
task，CancelledError 抛在业务 await 点）。

放 src 正规包（非 tests 目录）：temporalio workflow sandbox 按模块名重新
import workflow 类——tests 目录的模块名解析（tests.runtime.*）在 sandbox 下
不可靠。

_SENTINEL 模块级字典是测试断言面（同进程 WorkflowEnvironment.start_local 共享
worker，直接可读）。
"""
import asyncio
from datetime import timedelta

from temporalio import activity, workflow

_SENTINEL: dict = {}
_SEQ = 0


async def heartbeat_loop(interval: float) -> None:
    """最小心跳循环（探针内联版，与 runtime/temporal_heartbeat.py 实现解耦）。"""
    while True:
        await asyncio.sleep(interval)
        activity.heartbeat()


@activity.defn
async def probe_activity(use_heartbeat: bool) -> str:
    global _SEQ
    _SEQ += 1
    me = f"a{_SEQ}-hb{int(use_heartbeat)}"
    _SENTINEL.setdefault("started", []).append(me)
    hb = asyncio.create_task(heartbeat_loop(0.2)) if use_heartbeat else None
    try:
        await asyncio.sleep(120)
    except asyncio.CancelledError:
        _SENTINEL.setdefault("cancelled", []).append(me)
        raise
    finally:
        if hb is not None:
            hb.cancel()
    return "activity-done"


@workflow.defn
class CancelProbeWorkflow:
    @workflow.run
    async def run(self, use_heartbeat: bool) -> str:
        await workflow.execute_activity(
            probe_activity, use_heartbeat,
            start_to_close_timeout=timedelta(minutes=2),
            # 有意不设 heartbeat_timeout——裁决取消传递是否依赖它
        )
        return "done"


@workflow.defn
class CancelSwallowWorkflow:
    """复现 whitebox workflows.py:685 的 non-fatal 吞取消形态（2026-08-28 事故）。

    第一个 activity 的 cancel 异常被 except Exception 吞掉（模拟
    write_agent_poc 的 non-fatal 降级），workflow 继续起第二个（带心跳）
    activity——裁决：server 是否对「cancel 被吞后新起的 activity」重发取消。
    """

    @workflow.run
    async def run(self) -> str:
        self._stage = "phase1"
        try:
            await workflow.execute_activity(
                probe_activity, False,
                start_to_close_timeout=timedelta(minutes=2),
            )
        except Exception as exc:  # noqa: BLE001 — 模拟 non-fatal 吞取消
            self._stage = f"swallowed: {exc}"
        self._stage = "phase2"
        await workflow.execute_activity(
            probe_activity, True,  # 第二个 activity 带心跳
            start_to_close_timeout=timedelta(minutes=2),
        )
        return "survived"

    @workflow.query
    def stage(self) -> str:
        return self._stage
