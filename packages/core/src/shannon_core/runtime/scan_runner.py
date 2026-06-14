"""共享扫描运行时：优雅退出（SIGINT 双击 + Temporal 协作式取消）。

设计见 docs/superpowers/specs/2026-06-15-graceful-shutdown-design.md。
清理范围：只关连接/子进程 + 还原临时注入配置，不删 deliverables / workflow.log。
"""

import asyncio
import os
import signal


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
