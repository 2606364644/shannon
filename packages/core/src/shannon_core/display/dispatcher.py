"""DisplayDispatcher — fans a DisplayEvent out to every attached renderer."""
from __future__ import annotations

from shannon_core.display.events import DisplayEvent


class DisplayDispatcher:
    """Holds a list of renderers and forwards each event to all of them."""

    def __init__(self, renderers: list) -> None:
        # Typed loosely as list to avoid importing the Protocol at runtime;
        # each element must satisfy the Renderer protocol (async render(event)).
        self._renderers = list(renderers)

    async def dispatch(self, event: DisplayEvent) -> None:
        for renderer in self._renderers:
            await renderer.render(event)

    def add(self, renderer) -> None:
        self._renderers.append(renderer)