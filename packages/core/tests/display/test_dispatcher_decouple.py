"""日志总线解耦契约测试（spec 2026-08-05-multi-scan-concurrency-fix §3.1 / §4.1）。

根因：业务 activity（如 risk-scoring）经 track_step → dispatcher.dispatch 同步
await renderer 写盘，被磁盘 iowait / aiofiles 线程池饱和阻塞 → start_to_close
超时被 PRODUCTION_RETRY 放大成数十分钟卡死。

解耦契约：dispatch 把事件塞进队列后立即返回（微秒级），由后台 drain task 串行
render。业务永远不等磁盘写日志。
"""
import asyncio
import contextlib

from supernova_core.display.dispatcher import DisplayDispatcher
from supernova_core.display.events import PhaseEvent


def _evt(phase: str = "p") -> PhaseEvent:
    return PhaseEvent(timestamp="t", category="PHASE", phase=phase, event="start")


async def test_dispatch_does_not_block_on_slow_renderer():
    """慢 renderer 下 dispatch 必须微秒返回——业务不等磁盘写日志（核心契约）。"""

    class _SlowRenderer:
        async def render(self, event) -> None:
            await asyncio.sleep(0.3)  # 模拟磁盘 iowait 阻塞

    d = DisplayDispatcher([_SlowRenderer()])
    await d.start()
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    await d.dispatch(_evt())
    elapsed = loop.time() - t0
    assert elapsed < 0.05, f"dispatch 被慢 renderer 阻塞了 {elapsed:.3f}s（应微秒返回）"
    # 收尾：cancel drain，不等慢 render 排空
    d._drain_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await d._drain_task


async def test_dispatch_drops_when_queue_full():
    """队列满 → drop 当前事件 + 计数告警，绝不阻塞业务。"""

    class _HangingRenderer:
        async def render(self, event) -> None:
            await asyncio.sleep(10)  # drain 卡在首事件，队列迅速填满

    d = DisplayDispatcher([_HangingRenderer()], queue_maxsize=2)
    await d.start()
    ev = _evt()
    await d.dispatch(ev)
    await asyncio.sleep(0.05)  # 让 drain 取走首事件并卡在 render，腾出队列容量
    # drain 卡住不再取 → 连续 dispatch 填满（2）后必 drop（3），且不阻塞
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    for _ in range(5):
        await d.dispatch(ev)
    elapsed = loop.time() - t0
    assert elapsed < 0.5, f"队列满时 dispatch 阻塞了 {elapsed:.2f}s（应 drop 返回）"
    assert d.dropped_count >= 1
    d._drain_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await d._drain_task


async def test_drain_preserves_fifo_order():
    """单 drain task 串行 render，事件顺序与发出一致（events.ndjson 顺序不变）。"""

    class _Recorder:
        def __init__(self):
            self.events = []

        async def render(self, event) -> None:
            self.events.append(event)

    r = _Recorder()
    d = DisplayDispatcher([r])
    await d.start()
    events = [_evt(f"p{i}") for i in range(5)]
    for e in events:
        await d.dispatch(e)
    await d.close()  # graceful 排空
    assert [e.phase for e in r.events] == ["p0", "p1", "p2", "p3", "p4"]


async def test_close_drains_pending_events():
    """graceful close：排空队列再退，正常退出不丢日志。"""

    class _Recorder:
        def __init__(self):
            self.events = []

        async def render(self, event) -> None:
            self.events.append(event)

    r = _Recorder()
    d = DisplayDispatcher([r])
    await d.start()
    await d.dispatch(_evt())  # 入队后立即 close，事件尚未 render
    await d.close()
    assert len(r.events) == 1, "close 前入队的事件应在排空后被渲染"


async def test_dispatchers_are_isolated_per_scan():
    """两个 dispatcher 各自队列/drain 互不串扰（per-scan 隔离）。"""

    class _Recorder:
        def __init__(self):
            self.events = []

        async def render(self, event) -> None:
            self.events.append(event)

    r1, r2 = _Recorder(), _Recorder()
    d1, d2 = DisplayDispatcher([r1]), DisplayDispatcher([r2])
    await d1.start()
    await d2.start()
    await d1.dispatch(_evt("a"))
    await d2.dispatch(_evt("b"))
    await d1.close()
    await d2.close()
    assert [e.phase for e in r1.events] == ["a"]
    assert [e.phase for e in r2.events] == ["b"]


async def test_close_awaits_cancelled_drain_task():
    """close 返回后 drain task 必须 done：cancel() 只请求取消，不 await 的话 task
    可能尚未被调度就随 dispatcher 失去引用、GC 回收 pending task ->
    "Task was destroyed but it is pending!"（2026-08-18 worker 容器实测）。"""
    d = DisplayDispatcher([])
    await d.start()
    task = d._drain_task
    assert task is not None and not task.done()
    await d.close()
    assert task.done(), "close 返回时 drain task 应已终止（非 pending）"
    assert d._drain_task is None, "close 应清掉引用（防泄漏 + 二次 start 可重建）"
    await d.close()  # 二次 close 幂等不抛


async def test_renderer_error_does_not_break_drain_or_close():
    """单 renderer render 抛异常不能卡死 drain——否则 close().join() 死锁在
    未 task_done 的队列项上（磁盘满/权限错时 StructuredEventRenderer 会抛）。"""

    class _BoomRenderer:
        async def render(self, event) -> None:
            raise RuntimeError("disk full")

    class _Recorder:
        def __init__(self):
            self.events = []

        async def render(self, event) -> None:
            self.events.append(event)

    boom, rec = _BoomRenderer(), _Recorder()
    d = DisplayDispatcher([boom, rec])  # boom 在前抛异常,rec 在后仍应收到
    await d.start()
    await d.dispatch(_evt())
    await d.dispatch(_evt())
    await d.close()  # 不死锁
    assert len(rec.events) == 2, "boom 崩了不应阻断 rec 或 drain"
