import asyncio

from supernova_core.display.dispatcher import DisplayDispatcher
from supernova_core.display.events import PhaseEvent


class _RecordingRenderer:
    def __init__(self):
        self.events = []

    async def render(self, event) -> None:
        self.events.append(event)


async def test_dispatch_fans_out_to_all_renderers():
    r1, r2 = _RecordingRenderer(), _RecordingRenderer()
    dispatcher = DisplayDispatcher([r1, r2])
    await dispatcher.start()
    evt = PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start")
    await dispatcher.dispatch(evt)
    await dispatcher.close()
    assert r1.events == [evt]
    assert r2.events == [evt]


async def test_dispatch_with_no_renderers_is_noop():
    dispatcher = DisplayDispatcher([])
    await dispatcher.start()
    evt = PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start")
    # Must not raise
    await dispatcher.dispatch(evt)
    await dispatcher.close()


class _OrderRecordingRenderer:
    """Records start/end of each render call to detect interleaving."""
    def __init__(self, log: list, tag: str) -> None:
        self._log = log
        self._tag = tag

    async def render(self, event) -> None:
        self._log.append(f"start-{self._tag}")
        await asyncio.sleep(0)  # yield to force potential interleaving
        self._log.append(f"end-{self._tag}")


async def test_dispatch_serializes_concurrent_events():
    log: list[str] = []
    r = _OrderRecordingRenderer(log, "A")
    d = DisplayDispatcher([r])
    await d.start()
    ev = PhaseEvent(timestamp="t", category="PHASE", phase="p", event="start")
    await asyncio.gather(d.dispatch(ev), d.dispatch(ev))
    await d.close()
    # 单 drain task 串行 render → 不交错（解耦后由单消费者保证，非锁）
    assert log == ["start-A", "end-A", "start-A", "end-A"]
