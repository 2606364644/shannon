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
from shannon_core.display.formatters import agent_prefix, format_duration


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
        else:
            cells.append(Text(f" · {snap.completed_count} done", style="green"))
        cells.append(Text(f" · {elapsed}"))
        cells.append(Text(f" · ${snap.total_cost:.4f}", style="yellow"))

        row1 = Table.grid()
        row1.add_row(*cells)

        rows = [Text("─" * options.max_width, style="dim"), row1]
        if running:
            # 每个 running agent 一行；label 优先 step intent，否则 agent 短前缀；
            # action 优先当前工具，其次 turn 文本，再次 "running..."。
            # Table.grid() 用自然宽度，避免 expand-to-width 拉大间隙。
            for a in running:
                intent = snap.unit_intent.get(a.name)
                label = intent or agent_prefix(a.name)
                action = a.last_action_detail or a.last_turn_text or "running..."
                grid = Table.grid()
                grid.add_row(Spinner("dots"),
                             Text(f" {label} t{a.turn}  {action}", style="blue"))
                rows.append(grid)
        elif snap.running_units:
            # 无 running agent 但有 running step（如 code-index 这类非 agent 单元）：
            # 保留旧行为，显示运行中单元名。
            grid = Table.grid()
            grid.add_row(Spinner("dots"),
                         Text(" " + " · ".join(snap.running_units), style="blue"))
            rows.append(grid)
        return Group(*rows)
