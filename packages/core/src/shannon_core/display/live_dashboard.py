"""LiveDashboardRenderer — bottom dashboard renderer for the live scan.

Dual role:
  * dispatcher Renderer: async render(event) folds the event into a new
    immutable DashboardState snapshot via atomic reference swap.
  * Rich renderable: __rich_console__ builds the dashboard from the latest
    snapshot + live elapsed. Rich's Live refresh thread re-invokes
    __rich_console__ each tick, so the dashboard animates between events
    (spinner frames, ticking elapsed) without any per-event update call.

Concurrency: _snapshot is mutated only on the event-loop thread (under the
dispatcher's lock) via atomic assignment; the Live refresh thread reads it.
GIL makes the reference swap atomic, so the refresh thread always sees a
complete snapshot.
"""
from __future__ import annotations

import time

from rich.console import Console, ConsoleOptions, RenderResult
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from shannon_core.display.dashboard_state import AgentRow, DashboardState
from shannon_core.display.events import DisplayEvent
from shannon_core.display.formatters import agent_prefix, format_duration

_DONE = "✓"
_FAILED = "✗"


class LiveDashboardRenderer:
    def __init__(self, console: Console) -> None:
        self._console = console
        self._snapshot: DashboardState = DashboardState()
        self._start_monotonic: float = time.monotonic()

    @property
    def snapshot(self) -> DashboardState:
        return self._snapshot

    async def render(self, event: DisplayEvent) -> None:
        self._snapshot = self._snapshot.apply(event)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield self._render()

    def _render(self) -> Table:
        snap = self._snapshot
        elapsed = int(time.monotonic() - self._start_monotonic)

        top = Table.grid(expand=True, padding=(0, 1))
        phase = snap.current_phase or "—"
        top.add_row(
            Text(f"Phase: {phase}", style="bold cyan"),
            Text(f"{snap.completed_count} done", style="green"),
            Text(f"{elapsed}s"),
            Text(f"${snap.total_cost:.4f}"),
        )

        frame = Table.grid(expand=True)
        frame.add_row(top)
        frame.add_row(Text("─" * 60, style="dim"))
        for row in snap.agents.values():
            frame.add_row(self._agent_line(row))
        return frame

    def _agent_line(self, row: AgentRow) -> Table:
        line = Table.grid(expand=True, padding=(0, 1))
        line.add_column(width=2)
        line.add_column(ratio=2)
        line.add_column(ratio=1)
        line.add_column(ratio=3)

        if row.status == "running":
            icon = Spinner("dots")
            mid = Text(f"t{row.turn}" if row.turn else "·")
        elif row.status == "done":
            icon = Text(_DONE, style="green")
            mid = Text(format_duration(row.duration_ms or 0))
        else:
            icon = Text(_FAILED, style="red")
            mid = Text(format_duration(row.duration_ms or 0))

        label = Text.assemble((f"{agent_prefix(row.name)} ", "bold"), row.name)
        detail = Text(row.last_action_detail or row.last_action or "")
        line.add_row(icon, label, mid, detail)
        return line
