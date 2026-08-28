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

from supernova_core.audit.session_registry import _resolve_wf_id

HEARTBEAT_FILENAME = "heartbeat"
CANCEL_REQUESTED_FILENAME = "cancel.requested"


def _default_interval() -> float:
    """心跳周期默认从 env 读(默认 30s);scan worker 各 pipeline 挂载时不显式传 interval
    即用此默认,经 SUPERNOVA_HEARTBEAT_INTERVAL_SECONDS 可调(spec §5)。"""
    return float(os.environ.get("SUPERNOVA_HEARTBEAT_INTERVAL_SECONDS", "30"))


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


# 终态 status:心跳自感知——session 进入终态后,即使 cancel 传播链断裂(temporal workflow
# cancel 未把取消信号可靠传到 run_heartbeat activity),心跳线程也下个周期自停。对齐 web 的
# _TERMINAL_STATUSES(core 不依赖 web,自持一份同口径集合)。
_TERMINAL_SESSION_STATUSES = frozenset(
    {"completed", "failed", "interrupted", "cancelled", "killed", "crashed"}
)


def _session_is_terminal(ws_dir: Path) -> bool:
    """读 session.json status,终态返回 True;无文件/读失败/非终态一律 False。

    心跳线程自感知终态的依据,独立于 cancel 传播链。best-effort:任何 IO/解析异常都视作
    「未终态」(继续跳),绝不误停正常 scan(session.json 尚未写/损坏时)。
    """
    session_file = Path(ws_dir) / "session.json"
    try:
        data = json.loads(session_file.read_text("utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    status = data.get("status")
    # 兼容 nested 格式(与 session.SessionManager.get_status 同口径)
    if status is None and isinstance(data.get("session"), dict):
        status = data["session"].get("status")
    return status in _TERMINAL_SESSION_STATUSES


# heartbeat 注册表 API: 供主线 activity(setup_display 启动 / finalize_summary 停止)调用,
# 替代曾用的 background activity(workflow.start_activity / asyncio.create_task 包的 run_heartbeat).
# 后者在 worker poll 到 task(server pendingActivities.state=STARTED) 却从不 dispatch handler →
# heartbeat 永不写(2026-07-23 hr_1784788700 回归, test_workflow_heartbeat_execution 钉死).
# 走主线 await activity 启动 daemon 线程, 绕过 background activity 的 dispatch 缺陷.
# P3c 阶段 3: _current_heartbeat 单例 → _HEARTBEATS dict 按 workflow_id 索引, 解除
# max_concurrent_workflow_tasks=1 硬钉; 多 scan 并发各自 daemon 互不影响。旧「ws_dir 变了先停旧」
# 分支(并发杀心跳元凶)删除——按 workflow_id 隔离后不同 workflow 各自独立。
_HEARTBEATS: dict[str, "HeartbeatManager"] = {}


async def start_heartbeat(ws_dir: Path | str, workflow_id: str | None = None) -> None:
    """启动 heartbeat daemon 线程(写 <ws_dir>/heartbeat). 由 setup_display(主线首个 await
    activity, 真机确认执行)调用. 幂等: 同 workflow_id + 同 ws_dir 已在跑则跳过."""
    wf_id = _resolve_wf_id(workflow_id)
    ws = Path(ws_dir)
    existing = _HEARTBEATS.get(wf_id)
    if existing is not None and existing._ws_dir == ws:
        return  # 同 workflow + 同 ws_dir, 幂等跳过(setup_display retry 等)
    mgr = HeartbeatManager(ws, on_cancel=None)
    await mgr.__aenter__()  # 写首个 heartbeat(消除空窗) + 起 daemon 线程
    _HEARTBEATS[wf_id] = mgr


async def stop_heartbeat(workflow_id: str | None = None) -> None:
    """停止 heartbeat(daemon 线程退出 + best-effort 删 heartbeat 文件). 由 finalize_summary
    调用. 即使未调, daemon _heartbeat_loop 终态自停(session.json 终态)兜底, 不残留."""
    wf_id = _resolve_wf_id(workflow_id)
    mgr = _HEARTBEATS.pop(wf_id, None)
    if mgr is not None:
        await mgr.__aexit__(None, None, None)


def snapshot_heartbeat_workflows() -> dict[str, Path]:
    """活跃 heartbeat 注册表快照(workflow_id -> ws_dir)。worker 容器协作取消桥消费
    (2026-08-28 取消失效治本方案 B)：扫各 ws_dir 的 cancel.requested 转发 temporal
    cancel。注册表生命周期 = setup_display 起 / finalize_summary 或终态自停 pop，
    快照天然只含「在跑」的 workflow（此前 session_recovery._sweep_stale_sessions
    直接读 _HEARTBEATS 私有 dict，本 helper 顺带公开化该访问面）。"""
    return {wf: mgr._ws_dir for wf, mgr in list(_HEARTBEATS.items())}


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

        终态自停:每周期先查 session.json status,终态(cancelled/completed/failed 等)即
        set stop_event + 退出。不依赖 cancel 传播链(temporal workflow cancel 可能未传到本
        activity),否则靠 worker 常驻进程不退出而永久残留的 daemon 线程由此自愈(≤1 周期)。
        """
        while not self._stop_event.wait(self._interval):
            if _session_is_terminal(self._ws_dir):
                self._stop_event.set()
                return
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
