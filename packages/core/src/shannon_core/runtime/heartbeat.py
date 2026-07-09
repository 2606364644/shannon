"""scan worker 进程级心跳 + 协作式取消监听。

设计见 docs/superpowers/specs/2026-07-09-web-scan-liveness-deep-rework-design.md §4.1/§4.4。

心跳 task 是 worker 进程级的独立 asyncio task,**不参与 Temporal workflow/activity 调度**——
这是「不被 LLM 卡顿影响」的保证:worker 活着 event loop 就转,心跳就跳;worker 死(进程退出/
崩溃)event loop 停,心跳停写,heartbeat 文件 mtime 转 stale → web 据 mtime 判 interrupted。
与 wire_web_event_file(events.ndjson 落 workspace)同构:都是 worker 写文件、web 跨容器边界读。

协议:
- <ws_dir>/heartbeat:内容=单行 unix 时间戳(scan 进程 time.time())。temp+os.replace 原子写。
- <ws_dir>/cancel.requested:web 写入即「请求取消」;本 manager 周期检测到 → 调 on_cancel + 删它
  (删后不重复触发)。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
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
    """async context manager:周期写 heartbeat + 周期检测 cancel.requested。

    进入写初始 heartbeat(消除空窗)→ 起心跳 task(每 interval 写)→ 起 cancel 监听 task
    (每 interval 检测,检测到调 on_cancel 并删信号文件)。退出 cancel 两个 task + best-effort
    删 heartbeat(判活不依赖删除,ctrl+c/崩溃删不掉由 mtime stale 兜底)。

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

    @property
    def heartbeat_path(self) -> Path:
        return self._heartbeat

    async def __aenter__(self) -> "HeartbeatManager":
        await self._write_heartbeat()  # 初始 heartbeat,消除空窗
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        if self._on_cancel is not None:
            self._tasks.append(asyncio.create_task(self._cancel_loop()))
        return self

    async def __aexit__(self, *exc) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        with contextlib.suppress(OSError):
            self._heartbeat.unlink()  # best-effort 删;判活不依赖它

    async def _write_heartbeat(self) -> None:
        """原子写:temp 文件 + os.replace,并发读不读到半截。"""
        self._ws_dir.mkdir(parents=True, exist_ok=True)  # 确保(resume 传 name 时 ws_dir 可能未建)
        tmp = self._ws_dir / ".heartbeat.tmp"
        tmp.write_text(f"{time.time()}\n", encoding="utf-8")
        os.replace(tmp, self._heartbeat)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self._write_heartbeat()

    async def _cancel_loop(self) -> None:
        assert self._on_cancel is not None
        while True:
            await asyncio.sleep(self._interval)
            if self._cancel_signal.exists():
                with contextlib.suppress(OSError):
                    self._cancel_signal.unlink()  # 删后不重复触发
                self._on_cancel()
