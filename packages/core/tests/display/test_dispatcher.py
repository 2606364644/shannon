from shannon_core.display.dispatcher import DisplayDispatcher
from shannon_core.display.events import PhaseEvent


class _RecordingRenderer:
    def __init__(self):
        self.events = []

    async def render(self, event) -> None:
        self.events.append(event)


async def test_dispatch_fans_out_to_all_renderers():
    r1, r2 = _RecordingRenderer(), _RecordingRenderer()
    dispatcher = DisplayDispatcher([r1, r2])
    evt = PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start")
    await dispatcher.dispatch(evt)
    assert r1.events == [evt]
    assert r2.events == [evt]


async def test_dispatch_with_no_renderers_is_noop():
    dispatcher = DisplayDispatcher([])
    evt = PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start")
    # Must not raise
    await dispatcher.dispatch(evt)