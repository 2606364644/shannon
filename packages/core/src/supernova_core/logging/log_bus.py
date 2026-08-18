"""统一日志总线：LogBus 单例 + LogBusHandler。

spec/plan: docs/superpowers/specs/2026-07-08-unified-logging-facade-design.md
        / docs/superpowers/plans/2026-07-08-unified-log-bus.md（task 3/4）

生产线程的 ``LogBusHandler.emit`` 只做 ``prepare``（物化 message + 构造 LogEvent）
+ 分流，**不碰 console / stderr / rich / import**（sandbox 安全）：

- session 活跃（``is_attached``）→ ``queue.put_nowait(LogEvent)`` → event-loop drain
  task（task 4 的 ``attach`` 起的）批量 ``dispatch`` 到 RichConsoleRenderer +
  DiagnosticLogRenderer，与 PHASE/STEP 同 ``asyncio.Lock`` 序列化；
- 无 session（CLI 同步层 / 无 Live）→ ``DiagnosticLog.write_sync``（threading.Lock，
  无竞态）。

不挂 Formatter（渲染移到 event-loop renderer）；``emit`` 用 try/except 兜底确保
永不抛 → record 永远 handled → ``logging.lastResort``（硬编码 stderr）不触发。
不用标准 ``QueueListener.start()``：它自起 daemon 线程碰 console → 第三个线程 →
鬼影回归。console 输出只走 event-loop drain task。
"""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import queue as _queue
import traceback
from datetime import datetime

from .diagnostic_log import DiagnosticLog, format_diagnostic_line
from supernova_core.audit.session_registry import _resolve_wf_id

# diagnostic.log 句柄是**进程级**单例（2026-08-18 从 per-bus 提回）：configure_logging
# 的幂等 key（setup._configured_log_dir）本就是进程级，diagnostic 落在 per-wf bus 会
# 语义错位——跨 wf 切换时旧 bus 的句柄无人关（fd 泄漏），bus 被 pop 后残余 emit 的
# fallback 又变 no-op（丢日志）。模块级单例：换目录 configure 时关旧开新，天然正确。
_DIAGNOSTIC: DiagnosticLog | None = None


def configure_diagnostic(path) -> None:
    """(Re)point the process-wide diagnostic.log handle; close the prior one."""
    global _DIAGNOSTIC
    if _DIAGNOSTIC is not None:
        _DIAGNOSTIC.close()
    _DIAGNOSTIC = DiagnosticLog(path)


def write_fallback(event) -> None:
    """Production-thread fallback: no session → write straight to diagnostic.log.

    Called from ``LogBusHandler.emit`` (production thread) and from the drain
    task's dispatch-error降级. No-op when diagnostic was never configured.
    """
    if _DIAGNOSTIC is None:
        return
    _DIAGNOSTIC.write_sync(format_diagnostic_line(event))


def reset_diagnostic() -> None:
    """Close and drop the diagnostic handle（测试 teardown / 进程退出收尾用）。"""
    global _DIAGNOSTIC
    if _DIAGNOSTIC is not None:
        _DIAGNOSTIC.close()
        _DIAGNOSTIC = None


class _LogBus:
    """Per-workflow queue + dispatcher ref + drain task. State mutated on the
    event-loop thread (attach/detach) and read atomically (GIL) from production
    threads (``is_attached``). diagnostic 句柄不在此（进程级，见模块级 _DIAGNOSTIC）。"""

    def __init__(self) -> None:
        self.queue: _queue.Queue = _queue.Queue()
        self._dispatcher = None
        self._drain_task: asyncio.Task | None = None
        self._attached: bool = False

    @property
    def is_attached(self) -> bool:
        return self._attached

    async def attach(self, dispatcher) -> None:
        """Bind the session dispatcher and start the drain task (task 4 entry).

        Auto-mounts a DiagnosticLogRenderer onto the dispatcher so LogEvents routed
        through it also persist to diagnostic.log (the dispatcher's existing
        FileLogRenderer no-ops on LogEvent — clean separation from workflow.log).
        """
        self._dispatcher = dispatcher
        if _DIAGNOSTIC is not None:
            from supernova_core.display.file_renderer import DiagnosticLogRenderer
            dispatcher.add(DiagnosticLogRenderer(_DIAGNOSTIC))
        self._attached = True
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain_loop())

    async def drain_and_detach(self) -> None:
        """Final flush remaining queue, cancel drain task, mark detached (task 4)."""
        self._attached = False
        await self._drain_batch()  # final flush: dispatch remainder before teardown
        if self._drain_task is not None and not self._drain_task.done():
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
        self._drain_task = None
        self._dispatcher = None

    async def _drain_loop(self) -> None:
        try:
            while True:
                await self._drain_batch()
                await asyncio.sleep(0.05)  # only idle-yield to cut latency
        except asyncio.CancelledError:
            await self._drain_batch()  # best-effort final drain on cancel
            raise

    async def _drain_batch(self) -> None:
        batch = []
        while True:
            try:
                batch.append(self.queue.get_nowait())
            except _queue.Empty:
                break
        for event in batch:
            if self._dispatcher is not None:
                try:
                    await self._dispatcher.dispatch(event)
                except Exception:
                    # dispatch failed → degrade to file, never crash the drain loop
                    write_fallback(event)
            else:
                write_fallback(event)


# P3c 阶段 3：按 workflow_id 索引（替代进程级 LogBus 单例）。
# 每个 _LogBus 实例 = 一个 workflow 的独立 queue + drain task + diagnostic。
_BUSES: dict[str, _LogBus] = {}


def get_log_bus(workflow_id: str) -> _LogBus:
    """取（或创建）该 workflow 的 LogBus 实例（独立 queue + drain task + diagnostic）。"""
    return _BUSES.setdefault(workflow_id, _LogBus())


async def attach(dispatcher, *, workflow_id: str | None = None) -> None:
    """Attach dispatcher 到该 workflow 的 LogBus（显式 workflow_id 或 _resolve_wf_id 兜底）。"""
    await get_log_bus(_resolve_wf_id(workflow_id)).attach(dispatcher)


async def drain_and_detach(*, workflow_id: str | None = None) -> None:
    """Final flush + detach + 从 ``_BUSES`` 摘除该 workflow 的 bus。

    pop 后该 wf 的 ``LogBusHandler.emit`` 走新建占位 bus -> fallback 写**进程级**
    diagnostic（不丢，diagnostic 已不在 bus 上）。不 pop 则 bus 对象随 ``_BUSES``
    （setdefault 只增不减）永久累积（2026-08-18）。
    """
    wf_id = _resolve_wf_id(workflow_id)
    bus = _BUSES.pop(wf_id, None)
    if bus is not None:
        await bus.drain_and_detach()


def is_attached(*, workflow_id: str | None = None) -> bool:
    bus = _BUSES.get(_resolve_wf_id(workflow_id))
    return bus.is_attached if bus else False


class _LogBusProxy:
    """兼容层：现有 ``LogBus.attach`` / ``LogBus.queue`` / ``LogBus.is_attached`` /
    ``LogBus.configure_diagnostic`` 等调用零改动——``__getattr__`` 把属性/方法访问转发到
    当前 workflow 的 _LogBus 实例（经 ``_resolve_wf_id()`` 查表，activity 体内自动拿
    workflow_id）。与 ``LogBusHandler.emit`` 的动态路由共用同一 ``_resolve_wf_id``，
    故 attach 的 bus 与 emit 路由的 bus 天然一致（同线程同 context 同结果）。

    跨线程的 ``LogBusHandler`` 不走代理（emit 时按 ``_resolve_wf_id`` 动态 ``get_log_bus``）。
    """

    def __getattr__(self, name):
        if name == "drain_and_detach":
            # 收尾必须走模块级函数（含 _BUSES.pop，防 bus 残留累积）；
            # 直接转发实例方法会绕过 pop（2026-08-18）。
            async def _drain_via_module() -> None:
                await drain_and_detach()
            return _drain_via_module
        if name in ("configure_diagnostic", "_diagnostic"):
            # diagnostic 已是进程级单例（不在 per-wf bus 上），转发模块级状态。
            if name == "configure_diagnostic":
                return configure_diagnostic
            return _DIAGNOSTIC
        return getattr(get_log_bus(_resolve_wf_id()), name)

    def __setattr__(self, name, value):
        # set 也转发到当前 workflow 的 bus：P3c 前单例时代 ``LogBus._x = v`` 直接改
        # 单例；改代理后若落 proxy instance __dict__ 则不触达真实 bus，且后续读命中
        # instance dict 不再走 __getattr__ -> 状态被毒化（test_logging_setup 旧版
        # fixture 曾因此把 LogBus._diagnostic 永久读成 None，2026-08-18）。
        if name == "_diagnostic":
            # diagnostic 单例的写（旧 fixture 的 ``LogBus._diagnostic = None``）转发
            # 模块级，语义与读对齐。
            global _DIAGNOSTIC
            _DIAGNOSTIC = value
            return
        setattr(get_log_bus(_resolve_wf_id()), name, value)


LogBus = _LogBusProxy()


class LogBusHandler(logging.handlers.QueueHandler):
    """Root handler replacing StreamHandler(stderr)+FileHandler.

    On the production thread ``emit`` only ``prepare``s the record and routes by
    ``is_attached`` — it never touches console/stderr/rich/import, so the workflow
    sandbox thread can emit safely under ``redirect_stderr=False``. No Formatter is
    attached (rendering moves to the event-loop renderer).
    """

    def __init__(self, bus: _LogBus | None = None) -> None:
        # P3c 阶段 3：root 共享单 handler，emit 时按 _resolve_wf_id() 动态路由到对应
        # workflow 的 bus（多 scan 并发不串台）。bus 参数供测试注入；默认占位实例，
        # emit 不依赖 self._bus（动态 get_log_bus）。
        self._bus = bus if bus is not None else _LogBus()
        super().__init__(self._bus.queue)

    def prepare(self, record):  # type: ignore[override]
        """Materialize the message + build a LogEvent. Runs on the production thread,
        so it must stay pure-string (no rich/import). try/except keeps it robust."""
        try:
            msg = record.getMessage()
        except Exception:
            msg = record.msg if isinstance(record.msg, str) else str(record.msg)
        exc_txt = None
        if record.exc_info:
            exc_txt = "".join(traceback.format_exception(*record.exc_info))
        elif record.exc_text:
            exc_txt = record.exc_text
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        from supernova_core.display.events import LogEvent
        return LogEvent(
            timestamp=ts, category=record.levelname,
            logger_name=record.name, level=record.levelname,
            message=msg, exc_txt=exc_txt,
        )

    def emit(self, record):  # type: ignore[override]
        try:
            event = self.prepare(record)
            # P3c 阶段 3：按当前线程 _resolve_wf_id 动态路由到对应 workflow 的 bus。
            # async/to_thread 线程内 temporalio activity context 是 contextvar、被
            # asyncio.to_thread 复制传播 → activity.info() 可用 → 正确 workflow_id。
            bus = get_log_bus(_resolve_wf_id())
            if bus.is_attached:
                bus.queue.put_nowait(event)   # session active → queue for drain task
            else:
                write_fallback(event)          # no session → diagnostic.log（进程级）
        except Exception:
            # Never raise: an unhandled record would trip logging.lastResort (stderr).
            self.handleError(record)
