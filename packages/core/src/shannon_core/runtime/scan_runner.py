"""共享扫描运行时：优雅退出（SIGINT 双击 + Temporal 协作式取消）。

设计见 docs/superpowers/specs/2026-06-15-graceful-shutdown-design.md。
清理范围：只关连接/子进程 + 还原临时注入配置，不删 deliverables / workflow.log。
"""

import asyncio
import contextlib
import os
import signal
from typing import Any

from temporalio.client import Client
from temporalio.worker import Worker

from shannon_core.services.temporal_infra import generate_task_queue


class ScanCancelled(Exception):
    """扫描被用户中断（Ctrl+C / SIGTERM）时由 run_scan_graceful 抛出。"""


class ShutdownController:
    """管理 SIGINT 双击 + SIGTERM 直接优雅的退出语义。

    - 第 1 次 SIGINT：set 事件（主协程 await wait() 醒来走取消流程）。
    - 第 2 次 SIGINT：os._exit(130) 立即强制退出。
    - SIGTERM：直接 set 事件（不计数；docker stop / kill 的确定性终止）。
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._count = 0
        self._loop = None

    def install(self, loop: asyncio.AbstractEventLoop) -> None:
        """在给定 event loop 上注册信号 handler（仅 Unix）。"""
        self._loop = loop
        loop.add_signal_handler(signal.SIGINT, self._on_signal, signal.SIGINT)
        loop.add_signal_handler(signal.SIGTERM, self._on_signal, signal.SIGTERM)

    def _on_signal(self, signum: int) -> None:
        if signum == signal.SIGTERM:
            self._trigger_graceful()
            return
        # SIGINT：双击语义
        self._count += 1
        if self._count >= 2:
            self._force_exit()
        else:
            self._trigger_graceful()

    def _trigger_graceful(self) -> None:
        if not self._event.is_set():
            print("\n正在优雅取消…（再按一次 Ctrl+C 立即退出）", flush=True)
            self._event.set()

    def _force_exit(self) -> None:
        print("\n强制退出", flush=True)
        os._exit(130)

    def is_set(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def uninstall(self) -> None:
        if self._loop is None:
            return
        self._loop.remove_signal_handler(signal.SIGINT)
        self._loop.remove_signal_handler(signal.SIGTERM)


async def poll_progress(
    handle: Any,
    progress_type: Any,
    total: int = 13,
    interval_seconds: int = 30,
) -> None:
    """周期性查询 workflow 进度并打印一行（取代三个 worker 里复制的版本）。

    progress_type 由各 worker 注入（whitebox/blackbox 各自的 PipelineProgress），
    保持 core 不依赖上层包类型。
    """
    while True:
        try:
            progress = await handle.query("PipelineProgress", result_type=progress_type)
            elapsed = int(progress.elapsed_ms / 1000)
            phase = progress.current_phase or "unknown"
            agent = progress.current_agent or "none"
            completed = len(progress.completed_agents)
            print(
                f"[{elapsed}s] Phase: {phase} | Agent: {agent} | Completed: {completed}/{total}",
                flush=True,
            )
        except Exception:
            pass  # workflow 可能已完成或暂时不可查询
        await asyncio.sleep(interval_seconds)


async def run_scan_graceful(
    *,
    temporal_address: str,
    task_queue_prefix: str,
    workflow_cls,
    workflow_input,
    activities: list,
    progress_type,
    progress_total: int = 13,
    cancel_grace_seconds: float = 15.0,
) -> Any:
    """连接 Temporal、起 worker、跑 workflow；支持 SIGINT/SIGTERM 优雅取消。

    成功返回 workflow result；被用户中断时抛 ScanCancelled（由调用方捕获）。
    清理范围只含连接/子进程 + 临时注入配置（由 workflow 已有的 finally 触发），
    不删 deliverables / workflow.log。
    """
    client = await Client.connect(temporal_address)
    task_queue = generate_task_queue(task_queue_prefix)
    worker = Worker(
        client=client,
        task_queue=task_queue,
        workflows=[workflow_cls],
        activities=activities,
    )

    ctrl = ShutdownController()
    ctrl.install(asyncio.get_running_loop())

    async with worker:
        workflow_id = (
            getattr(workflow_input, "workspace_name", None)
            or f"{task_queue_prefix}-scan"
        )
        handle = await client.start_workflow(
            workflow_cls.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue,
        )
        poll_task = asyncio.create_task(
            poll_progress(handle, progress_type=progress_type, total=progress_total)
        )
        result_task = asyncio.ensure_future(handle.result())
        shutdown_wait_task = asyncio.create_task(ctrl.wait())
        try:
            await asyncio.wait(
                {result_task, shutdown_wait_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if ctrl.is_set():
                await _do_cancel(handle, result_task, cancel_grace_seconds)
                raise ScanCancelled()
            return result_task.result()
        finally:
            # result_task 故意不在此取消：
            #   - 正常路径：已被上面的 result_task.result() 消费
            #   - 取消路径：由 _do_cancel 的 wait_for 消费
            for task in (poll_task, shutdown_wait_task):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            ctrl.uninstall()


async def _do_cancel(handle, result_task, cancel_grace_seconds: float) -> None:
    """发协作式 cancel，并在 grace 期内等待结果；超时放弃等待（不 escalate）。"""
    print("正在取消 Temporal workflow…", flush=True)
    try:
        await handle.cancel()
    except Exception as exc:
        print(f"cancel 请求失败（忽略）: {exc}", flush=True)
    try:
        await asyncio.wait_for(result_task, timeout=cancel_grace_seconds)
    except asyncio.TimeoutError:
        print(
            f"{cancel_grace_seconds}s 内 workflow 未响应取消，放弃等待"
            f"（server 端 cancel 仍生效）",
            flush=True,
        )
    except Exception:
        # result_task 因 cancel 抛出的异常属预期，吞掉
        pass
