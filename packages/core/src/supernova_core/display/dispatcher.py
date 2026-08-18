"""DisplayDispatcher — fans a DisplayEvent out to every attached renderer.

Decoupled design (2026-08-05 multi-scan concurrency fix): ``dispatch`` enqueues
the event and returns immediately (microseconds); a single background drain
task renders events serially. Business activities (e.g. ``run_risk_scoring``)
never block on disk writes — the observation path (logging) no longer stalls the
value path (the scan). One dispatcher per scan.

Before this change ``dispatch`` held an ``asyncio.Lock`` and ``await``-ed every
renderer's ``render`` inline, so a slow disk flush under multi-scan iowait could
block the calling activity past its ``start_to_close_timeout``; the retry policy
then amplified a single timeout into tens of minutes of silence.
"""
from __future__ import annotations

import asyncio
import logging

from supernova_core.display.events import DisplayEvent

logger = logging.getLogger(__name__)


class DisplayDispatcher:
    """Holds a list of renderers and forwards each event to all of them.

    Events flow through a bounded FIFO queue to a single drain task, which
    renders them one at a time (so file writes / console prints never
    interleave). ``dispatch`` is non-blocking: when the queue is full it drops
    the incoming event and increments ``dropped_count`` rather than stalling the
    caller.
    """

    def __init__(self, renderers: list, queue_maxsize: int = 1000) -> None:
        self._renderers = list(renderers)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self._dropped = 0
        self._drain_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Spawn the background drain task (idempotent).

        Must be awaited before the first ``dispatch`` so events are consumed.
        """
        if self._drain_task is None:
            self._drain_task = asyncio.create_task(self._drain())

    async def dispatch(self, event: DisplayEvent) -> None:
        """Enqueue an event for the drain task; never blocks the caller.

        Queue full → drop this event + count it (preserves already-queued
        ordering) rather than blocking the business activity on disk.
        """
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            logger.warning(
                "log event dropped (queue full): %s", type(event).__name__)

    async def _drain(self) -> None:
        """Serially render queued events; a single consumer so ordering holds.

        One renderer raising must not break the drain (else ``close().join()``
        would deadlock on the un-``task_done``-d item) — aligned with the
        renderer-close-failure tolerance in WorkflowLogger.close.
        """
        while True:
            event = await self._queue.get()
            for renderer in self._renderers:
                try:
                    await renderer.render(event)
                except Exception:
                    logger.warning(
                        "renderer render failed: %s", type(renderer).__name__,
                        exc_info=True)
            self._queue.task_done()

    async def close(self) -> None:
        """Drain the queue (graceful — no loss on normal exit), then stop the task."""
        await self._queue.join()
        if self._drain_task is not None:
            self._drain_task.cancel()
            # cancel() 只请求取消：不 await 的话 task 可能尚未被调度就随 dispatcher
            # 失去外部引用，GC 回收 pending task -> "Task was destroyed but it is
            # pending!"（2026-08-18）。写法对齐 _LogBus.drain_and_detach（log_bus.py）。
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
            self._drain_task = None

    @property
    def dropped_count(self) -> int:
        """Number of events dropped because the queue was full (observability)."""
        return self._dropped

    def add(self, renderer) -> None:
        self._renderers.append(renderer)
