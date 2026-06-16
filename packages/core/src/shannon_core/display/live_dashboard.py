"""LiveDashboardRenderer — bottom status-line renderer for the live scan.

Dual role:
  * dispatcher Renderer: async render(event) folds the event into a new
    immutable DashboardState snapshot via atomic reference swap.
  * Rich renderable: __rich_console__ builds a single compact status line from
    the latest snapshot + live elapsed. Rich's Live refresh thread re-invokes
    __rich_console__ each tick, so the status line animates between events
    (spinner frames, ticking elapsed) without any per-event update call.

The status line carries: phase · completed-count · elapsed · cost, with the
currently-running agent(s) + spinner appended. A full-width dim rule sits
above it to separate it from the scrolling log region. This replaces the former
expand-to-width multi-row agent table (which stretched short tokens into big
gaps) and the hardcoded 60-char separator (which never matched terminal width).

Concurrency: _snapshot is mutated only on the event-loop thread (under the
dispatcher's lock) via atomic assignment; the Live refresh thread reads it.
GIL makes the reference swap atomic, so the refresh thread always sees a
complete snapshot.
"""
from __future__ import annotations

import time

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from shannon_core.display.dashboard_state import DashboardState
from shannon_core.display.events import DisplayEvent
from shannon_core.display.formatters import format_duration


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
        yield self._render(options)

    def _render(self, options: ConsoleOptions) -> Group:
        snap = self._snapshot
        elapsed = format_duration(int(time.monotonic() - self._start_monotonic) * 1000)
        running = [r for r in snap.agents.values() if r.status == "running"]

        cells: list = [Text(snap.current_phase or "—", style="bold cyan")]

        if snap.total_units > 0:
            cells.append(Text(f" · step {snap.completed_units}/{snap.total_units}", style="green"))
            running_unit_names = snap.running_units
        else:
            cells.append(Text(f" · {snap.completed_count} done", style="green"))
            running_unit_names = [r.name for r in running]

        cells.append(Text(f" · {elapsed}"))
        cells.append(Text(f" · ${snap.total_cost:.4f}", style="yellow"))

        if running_unit_names:
            cells += [Text("    "), Spinner("dots"),
                      Text(" " + " · ".join(running_unit_names), style="blue")]

        row = Table.grid()  # expand=False: cells take natural width, no big gaps
        row.add_row(*cells)

        return Group(
            Text("─" * options.max_width, style="dim"),  # spans real terminal width
            row,
        )
