"""DisplayDispatcher — fans a DisplayEvent out to every attached renderer."""
from __future__ import annotations

import asyncio

from shannon_core.display.events import DisplayEvent


class DisplayDispatcher:
    """Holds a list of renderers and forwards each event to all of them.

    A single asyncio.Lock serializes dispatch: concurrent events from parallel
    activities are rendered one at a time, so file writes / console prints /
    dashboard snapshot builds never interleave. One dispatcher per scan.
    """

    def __init__(self, renderers: list) -> None:
        self._renderers = list(renderers)
        self._lock = asyncio.Lock()

    async def dispatch(self, event: DisplayEvent) -> None:
        async with self._lock:
            for renderer in self._renderers:
                await renderer.render(event)

    def add(self, renderer) -> None:
        self._renderers.append(renderer)
