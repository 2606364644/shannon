"""Protocols decoupling renderers from concrete output targets."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    # Avoid a runtime import of events so this module is independently testable
    # before events.py exists.
    from supernova_core.display.events import DisplayEvent


@runtime_checkable
class LineWriter(Protocol):
    """Append-only async text sink. Satisfied structurally by LogStream."""

    async def write(self, text: str) -> None: ...


@runtime_checkable
class Renderer(Protocol):
    """Render a single DisplayEvent to some output."""

    async def render(self, event: DisplayEvent) -> None: ...
