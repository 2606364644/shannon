"""scan worker 进程级心跳 + 协作式取消监听。

设计见 docs/superpowers/specs/2026-07-09-web-scan-liveness-deep-rework-design.md §4.1/§4.4。

心跳写入跑在 **独立 daemon 线程**(time.sleep 周期写),**彻底脱离 worker 的 asyncio
event loop**——这是「不被 event loop 阻塞影响」的硬保证:worker 进程活着线程就转、心跳
就跳;worker 死(进程退出)daemon 线程随进程结束、heartbeat mtime 转 stale → web 据
mtime 判 interrupted。

为何不用 asyncio task(历史教训 2026-07-15 trip_1784116216):asyncio.sleep 驱动的心跳
task 与 run_code_index 等耗时 activity 共享同一 event loop;GitNexus taint/sink/source
分析的同步 CPU 密集段会阻塞 event loop 数十~数百秒,期间心跳 task 得不到调度、heartbeat
停写,超 90s freshness 阈值后 web 误判 interrupted(终态不可逆),而 worker 实际仍在
正常推进。改 daemon 线程后,event loop 再怎么被 CPU 密集段卡,心跳都准时写。

协议:
- <ws_dir>/heartbeat:内容=单行 unix 时间戳(scan 进程 time.time())。temp+os.replace 原子写。
- <ws_dir>/cancel.requested:web 写入即「请求取消」;cancel 监听 task 周期检测到 → 调
  on_cancel + 删它(删后不重复触发)。cancel 监听仍用 asyncio task(低频协作式,非判活
  核心;event loop 阻塞时其响应延迟到阻塞段结束,可接受)。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

HEARTBEAT_FILENAME = "heartbeat"
CANCEL_REQUESTED_FILENAME = "cancel.requested"


def _default_interval() -> float:
    """心跳周期默认从 env 读(默认 30s);scan worker 各 pipeline 挂载时不显式传 interval
    即用此默认,经 SHANNON_HEARTBEAT_INTERVAL_SECONDS 可调(spec §5)。"""
    return float(os.environ.get("SHANNON_HEARTBEAT_INTERVAL_SECONDS", "30"))


def mark_owner_if_unset(ws_dir: Path, owner: str) -> None:
    """标 session.json owner,仅当未设。web 起 scan 时 scan_manager 先写 owner=web,worker
    (子进程)读到已设则不覆盖;CLI 起 scan 未设 → 标 host。owner 只服务 cancel 分轨诊断。"""
    session_file = Path(ws_dir) / "session.json"
    try:
        data: dict = {}
        if session_file.exists():
            loaded = json.loads(session_file.read_text("utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        if data.get("owner"):
            return  # 已设(web 起 scan_manager 写的 web),不覆盖
        data["owner"] = owner
        session_file.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, ValueError):
        pass


class HeartbeatManager:
    """async context manager:周期写 heartbeat(daemon 线程,脱离 event loop)+ 周期检测
    cancel.requested(asyncio task)。

    进入写初始 heartbeat(消除空窗)→ 起 daemon 心跳线程(每 interval 写,独立于 event
    loop)→ 起 cancel 监听 task(每 interval 检测,检测到调 on_cancel 并删信号文件)。退出
    置停止信号 + join 心跳线程 + cancel 监听 task + best-effort 删 heartbeat(判活不依赖
    删除,ctrl+c/崩溃删不掉由 mtime stale 兜底)。

    on_cancel 由各 pipeline 注入:whitebox/blackbox 传触发 ShutdownController 的回调(复用双击
    SIGINT 的整套 graceful 取消),multi 传取消传播回调。
    """

    def __init__(
        self,
        ws_dir: Path,
        interval: float | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._ws_dir = Path(ws_dir)
        self._interval = interval if interval is not None else _default_interval()
        self._on_cancel = on_cancel
        self._heartbeat = self._ws_dir / HEARTBEAT_FILENAME
        self._cancel_signal = self._ws_dir / CANCEL_REQUESTED_FILENAME
        self._tasks: list[asyncio.Task] = []
        # 心跳线程停止信号 + 线程句柄(daemon,脱离 event loop)
        self._stop_event = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    @property
    def heartbeat_path(self) -> Path:
        return self._heartbeat

    async def __aenter__(self) -> "HeartbeatManager":
        self._write_heartbeat()  # 初始 heartbeat,消除空窗(同步,不依赖 event loop)
        # daemon 线程跑心跳 loop:event loop 被同步 CPU 密集段阻塞时仍准时写(进程级独立)。
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="shannon-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        if self._on_cancel is not None:
            self._tasks.append(asyncio.create_task(self._cancel_loop()))
        return self

    async def __aexit__(self, *exc) -> None:
        # 1) 通知心跳线程停 + 等其退出(stop_event.set 后其 wait 立即返回,join 通常毫秒级)
        self._stop_event.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=5.0)
            self._heartbeat_thread = None
        # 2) cancel 监听 task(若有)
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        with contextlib.suppress(OSError):
            self._heartbeat.unlink()  # best-effort 删;判活不依赖它

    def _write_heartbeat(self) -> None:
        """原子写:temp 文件 + os.replace,并发读不读到半截。同步(daemon 线程内调用,
        脱离 event loop)。"""
        self._ws_dir.mkdir(parents=True, exist_ok=True)  # 确保(resume 传 name 时 ws_dir 可能未建)
        tmp = self._ws_dir / ".heartbeat.tmp"
        tmp.write_text(f"{time.time()}\n", encoding="utf-8")
        os.replace(tmp, self._heartbeat)

    def _heartbeat_loop(self) -> None:
        """daemon 线程主体:周期写 heartbeat,独立于 asyncio event loop。

        worker 进程活→线程转→心跳跳;进程退出→daemon 线程随进程结束。用
        _stop_event.wait(interval) 做可中断周期(False=超时该写了,True=被请求停止)。
        """
        while not self._stop_event.wait(self._interval):
            try:
                self._write_heartbeat()
            except OSError:
                pass  # best-effort,下个周期重试(磁盘满/权限等瞬态不致命)

    async def _cancel_loop(self) -> None:
        assert self._on_cancel is not None
        while True:
            await asyncio.sleep(self._interval)
            if self._cancel_signal.exists():
                with contextlib.suppress(OSError):
                    self._cancel_signal.unlink()  # 删后不重复触发
                self._on_cancel()
