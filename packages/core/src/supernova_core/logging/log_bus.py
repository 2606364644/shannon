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


class _LogBus:
    """Module-level singleton holding the queue, dispatcher ref, drain task, and
    diagnostic handle. State mutated on the event-loop thread (attach/detach) and
    read atomically (GIL) from production threads (``is_attached``)."""

    def __init__(self) -> None:
        self.queue: _queue.Queue = _queue.Queue()
        self._dispatcher = None
        self._drain_task: asyncio.Task | None = None
        self._diagnostic: DiagnosticLog | None = None
        self._attached: bool = False

    @property
    def is_attached(self) -> bool:
        return self._attached

    def configure_diagnostic(self, path) -> None:
        """(Re)point the diagnostic.log handle; close the prior one. Idempotent-safe."""
        if self._diagnostic is not None:
            self._diagnostic.close()
        self._diagnostic = DiagnosticLog(path)

    def write_fallback(self, event) -> None:
        """Production-thread fallback: no session → write straight to diagnostic.log.

        Called from ``LogBusHandler.emit`` (production thread) and from the drain
        task's dispatch-error降级. No-op when diagnostic was never configured.
        """
        if self._diagnostic is None:
            return
        self._diagnostic.write_sync(format_diagnostic_line(event))

    async def attach(self, dispatcher) -> None:
        """Bind the session dispatcher and start the drain task (task 4 entry).

        Auto-mounts a DiagnosticLogRenderer onto the dispatcher so LogEvents routed
        through it also persist to diagnostic.log (the dispatcher's existing
        FileLogRenderer no-ops on LogEvent — clean separation from workflow.log).
        """
        self._dispatcher = dispatcher
        if self._diagnostic is not None:
            from supernova_core.display.file_renderer import DiagnosticLogRenderer
            dispatcher.add(DiagnosticLogRenderer(self._diagnostic))
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
                    self.write_fallback(event)
            else:
                self.write_fallback(event)


LogBus = _LogBus()


class LogBusHandler(logging.handlers.QueueHandler):
    """Root handler replacing StreamHandler(stderr)+FileHandler.

    On the production thread ``emit`` only ``prepare``s the record and routes by
    ``is_attached`` — it never touches console/stderr/rich/import, so the workflow
    sandbox thread can emit safely under ``redirect_stderr=False``. No Formatter is
    attached (rendering moves to the event-loop renderer).
    """

    def __init__(self, bus: _LogBus | None = None) -> None:
        bus = bus or LogBus
        super().__init__(bus.queue)
        self._bus = bus

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
            if self._bus.is_attached:
                self.enqueue(event)            # session active → queue for drain task
            else:
                self._bus.write_fallback(event)  # no session → diagnostic.log
        except Exception:
            # Never raise: an unhandled record would trip logging.lastResort (stderr).
            self.handleError(record)
