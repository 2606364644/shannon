# Whitebox 实时日志展示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the existing display pipeline into the whitebox Temporal scan lifecycle, add a parallel-agent live dashboard, enable Rich stdout (TTY-autodetect + `--plain`), and retire the `print()` poller — so `shannon-whitebox start` shows a scrolling event log + bottom dashboard, surpassing the original Shannon on parallel-agent visibility.

**Architecture:** A worker-level `AuditSession` singleton (owned by `run_scan`) holds a shared Rich `Console` + `Live` for the whole scan. Activities reach it via `get_audit_session()`. Events flow through the existing `WorkflowLogger → DisplayDispatcher → renderers` pipeline, with a new stateful `LiveDashboardRenderer` (immutable `DashboardState` snapshots, atomic ref swap) added as a third renderer. A global `asyncio.Lock` in the dispatcher serializes concurrent events. Progress is event-driven (zero polling).

**Tech Stack:** Python 3.13+, Rich (`Console`/`Live`/`Panel`/`Table`/`Spinner`), Temporal (python SDK, async activities on one event loop), pytest + pytest-asyncio (asyncio_mode=auto), uv workspace.

**Spec:** `docs/superpowers/specs/2026-06-15-whitebox-live-display-design.md`

**Key facts established (do not re-derive):**
- `DisplayDispatcher.dispatch` is `for r in renderers: await r.render(event)` — **no cross-event lock** (Task 4 adds it).
- `LogStream.write` is async + flush-per-call → concurrent writes interleave without the lock.
- Rich `Live` requires a `__rich_console__` renderable (not a bare callable); its refresh thread re-renders each tick; non-TTY → no refresh thread.
- `run_agent(input)` derives `agent_name = AgentName(input.workspace_name)`; the workflow sets `workspace_name` per phase.
- Temporal retry attempt: `activity.info().attempt`.
- `AgentMetrics`: `duration_ms, cost_usd, num_turns, model, ...`. `AgentEndResult`: `success, duration_ms, cost_usd, attempt_number, model, error`.
- The audit_logger chain: activity → `executor.execute(audit_logger=)` → `run_claude_prompt` wraps in `ActivityToolAuditLogger` → `provider.call(audit_logger=)`. To reach the display we thread an explicit `tool_audit_logger` that bypasses that wrapping.
- `test_audit_session.py` has **4 pre-existing failures** (stale old-format assertions) — Task 1 fixes them.

---

## File Structure

**Created:**
- `packages/core/src/shannon_core/display/dashboard_state.py` — `DashboardState` immutable state machine (pure, no Rich)
- `packages/core/src/shannon_core/display/live_dashboard.py` — `LiveDashboardRenderer` (dispatcher Renderer + `__rich_console__` renderable)
- `packages/core/tests/display/test_dashboard_state.py`
- `packages/core/tests/display/test_live_dashboard.py`
- `packages/whitebox/src/shannon_whitebox/audit/session_tool_audit_logger.py` — `SessionToolAuditLogger` (core `ToolAuditLogger` → `AuditSession`)
- `packages/whitebox/src/shannon_whitebox/audit/session_registry.py` — `get_audit_session` / `set_audit_session` / `NullAuditSession`
- `packages/whitebox/tests/test_session_tool_audit_logger.py`
- `packages/whitebox/tests/test_session_registry.py`
- `packages/whitebox/tests/test_display_integration.py` — **L2 gate**

**Modified:**
- `packages/core/src/shannon_core/display/dispatcher.py` — add internal `asyncio.Lock`
- `packages/core/src/shannon_core/agents/tool_audit_logger.py` — add `log_assistant_turn`
- `packages/core/src/shannon_core/agents/message_dispatcher.py` — call `log_assistant_turn`
- `packages/core/src/shannon_core/agents/executor.py` — thread `tool_audit_logger` param
- `packages/core/src/shannon_core/agents/runner.py` — prefer passed `tool_audit_logger`
- `packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py` — `console`/`dashboard` params, attach `LiveDashboardRenderer`
- `packages/whitebox/src/shannon_whitebox/audit/session.py` — display config passthrough
- `packages/whitebox/src/shannon_whitebox/worker.py` — Console+Live lifecycle, register singleton, **delete `poll_workflow_progress`**
- `packages/whitebox/src/shannon_whitebox/cli/main.py` — `--plain` flag
- `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` — wire `get_audit_session` + `SessionToolAuditLogger` + `start/end_agent` + `log_error`; add `log_phase_start/complete` activities
- `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` — schedule phase-marker activities
- `packages/whitebox/tests/test_audit_session.py` — fix 4 stale assertions (Task 1)

---

## Phase 0 — Clear the landmine

### Task 1: Fix stale `test_audit_session.py` (4 pre-existing failures)

**Files:**
- Modify: `packages/whitebox/tests/test_audit_session.py`

The `WorkflowLogger` was refactored to emit DisplayEvents (new format), but these 4 tests still assert the old `workflow_logger.py` format. Update them to the current `FileLogRenderer` output. Run first so the suite is green before building on `AuditSession`.

- [ ] **Step 1: Confirm the 4 failures**

Run: `uv run pytest packages/whitebox/tests/test_audit_session.py -q`
Expected: 4 FAILED (`test_log_event_dispatches_to_both_loggers`, `test_log_event_dispatches_llm_response`, `test_log_phase_start_and_complete`, `test_full_lifecycle`), 10 passed.

- [ ] **Step 2: Fix `test_log_phase_start_and_complete` (line ~117-118)**

Replace:
```python
    assert "[PHASE] recon started" in wf_content
    assert "[PHASE] recon completed" in wf_content
```
with:
```python
    assert "[PHASE] Starting recon" in wf_content
    assert "[PHASE] Completed recon" in wf_content
```

- [ ] **Step 3: Fix `test_log_event_dispatches_to_both_loggers` (line ~65)**

Replace:
```python
    assert "[TOOL] recon → Read(" in wf_content
```
with:
```python
    assert "[TOOL]  recon: Read:" in wf_content
    assert "file_path=/tmp/test" in wf_content
```

- [ ] **Step 4: Fix `test_log_event_dispatches_llm_response` (line ~76)**

Replace:
```python
    assert "[LLM] recon turn 1:" in wf_content
```
with:
```python
    assert "[LLM]   recon: Turn 1:" in wf_content
```

- [ ] **Step 5: Fix `test_full_lifecycle` (lines ~217-222)**

Replace the block:
```python
    assert "[PHASE] recon started" in wf
    assert "[AGENT] recon started" in wf
    assert "[TOOL] recon → Read(" in wf
    assert "[LLM] recon turn 1:" in wf
    assert "[AGENT] recon ended" in wf
    assert "[PHASE] recon completed" in wf
```
with:
```python
    assert "[PHASE] Starting recon" in wf
    assert "[AGENT] recon: Starting" in wf
    assert "[TOOL]  recon: Read:" in wf
    assert "[LLM]   recon: Turn 1:" in wf
    assert "[AGENT] recon: Completed" in wf
    assert "[PHASE] Completed recon" in wf
```

- [ ] **Step 6: Run the file — all green**

Run: `uv run pytest packages/whitebox/tests/test_audit_session.py -q`
Expected: 14 passed.

- [ ] **Step 7: Commit**

```bash
git add packages/whitebox/tests/test_audit_session.py
git commit -m "test(whitebox): fix stale audit_session assertions to new renderer format"
```

---

## Phase 1 — Core display components

### Task 2: `DashboardState` — pure immutable state machine

**Files:**
- Create: `packages/core/src/shannon_core/display/dashboard_state.py`
- Test: `packages/core/tests/display/test_dashboard_state.py`

Eats `DisplayEvent`s, produces a new immutable `DashboardState`. No Rich, no time — pure data. Elapsed is computed at render time by the renderer, not here.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/display/test_dashboard_state.py`:
```python
from shannon_core.display.dashboard_state import DashboardState, AgentRow
from shannon_core.display.events import (
    PhaseEvent, AgentEvent, ToolCallEvent, LlmTurnEvent, ErrorEvent, ResumeEvent,
)


def _phase(name: str) -> PhaseEvent:
    return PhaseEvent(timestamp="t", category="PHASE", phase=name, event="start")


def _agent(name: str, event: str = "start", **kw) -> AgentEvent:
    return AgentEvent(timestamp="t", category="AGENT", agent_name=name,
                      event=event, attempt=kw.get("attempt", 1),
                      duration_ms=kw.get("duration_ms"), cost_usd=kw.get("cost_usd"),
                      success=kw.get("success"), error=kw.get("error"))


def test_initial_state_is_empty():
    s = DashboardState()
    assert s.current_phase is None
    assert s.agents == {}
    assert s.completed_count == 0
    assert s.total_cost == 0.0


def test_phase_event_sets_current_phase():
    s = DashboardState().apply(_phase("vulnerability-analysis"))
    assert s.current_phase == "vulnerability-analysis"


def test_agent_start_creates_running_row():
    s = DashboardState().apply(_agent("injection-vuln", "start", attempt=1))
    row = s.agents["injection-vuln"]
    assert row.status == "running"
    assert row.attempt == 1
    assert s.completed_count == 0


def test_agent_end_marks_done_and_counts():
    s = (DashboardState()
         .apply(_agent("injection-vuln", "start"))
         .apply(_agent("injection-vuln", "end", duration_ms=5200, cost_usd=0.15, success=True)))
    row = s.agents["injection-vuln"]
    assert row.status == "done"
    assert row.duration_ms == 5200
    assert row.cost_usd == 0.15
    assert s.completed_count == 1
    assert s.total_cost == 0.15


def test_agent_end_failed_marks_failed():
    s = (DashboardState()
         .apply(_agent("xss-vuln", "start"))
         .apply(_agent("xss-vuln", "end", success=False, error="rate limit")))
    assert s.agents["xss-vuln"].status == "failed"
    assert s.agents["xss-vuln"].error == "rate limit"
    assert s.completed_count == 1  # terminal either way


def test_retry_updates_attempt_and_back_to_running():
    s = (DashboardState()
         .apply(_agent("xss-vuln", "start", attempt=1))
         .apply(_agent("xss-vuln", "end", success=False, error="boom"))
         .apply(_agent("xss-vuln", "start", attempt=2)))
    assert s.agents["xss-vuln"].status == "running"
    assert s.agents["xss-vuln"].attempt == 2


def test_tool_call_updates_last_action_and_turn_is_unaffected():
    s = (DashboardState()
         .apply(_agent("injection-vuln", "start"))
         .apply(ToolCallEvent(timestamp="t", category="TOOL", agent_name="injection-vuln",
                              tool_name="Bash", parameters={"command": "rg -n eval"})))
    assert s.agents["injection-vuln"].last_action == "Bash"
    assert s.agents["injection-vuln"].last_action_detail == "command=rg -n eval"


def test_llm_turn_updates_turn_count():
    s = (DashboardState()
         .apply(_agent("injection-vuln", "start"))
         .apply(LlmTurnEvent(timestamp="t", category="LLM", agent_name="injection-vuln",
                             turn=3, content="...")))
    assert s.agents["injection-vuln"].turn == 3


def test_resume_event_seeds_completed_agents():
    s = DashboardState().apply(ResumeEvent(
        timestamp="t", category="RESUME", previous_workflow_id="a", new_workflow_id="b",
        checkpoint_hash="h", completed_agents=["recon", "pre-recon"]))
    assert s.agents["recon"].status == "done"
    assert s.agents["pre-recon"].status == "done"
    assert s.completed_count == 2


def test_apply_is_immutable():
    s0 = DashboardState()
    s1 = s0.apply(_phase("recon"))
    assert s0.current_phase is None  # original unchanged
    assert s1.current_phase == "recon"


def test_unknown_event_is_noop():
    from shannon_core.display.events import WorkflowHeader
    s = DashboardState().apply(WorkflowHeader(timestamp="t", category="HEADER",
                                              workflow_id="w", target_url="u"))
    assert s == DashboardState()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_dashboard_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.display.dashboard_state'`.

- [ ] **Step 3: Implement `DashboardState`**

Create `packages/core/src/shannon_core/display/dashboard_state.py`:
```python
"""DashboardState — immutable pure-data state machine for the live dashboard.

Eats DisplayEvents and returns a new DashboardState. Holds NO rendering logic
and NO time calls, so it is fully deterministic and unit-testable in isolation.
Elapsed/clock is computed by the renderer at render time, not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from shannon_core.display.events import (
    AgentEvent, DisplayEvent, ErrorEvent, LlmTurnEvent, PhaseEvent,
    ResumeEvent, SummaryEvent, ToolCallEvent,
)
from shannon_core.display.formatters import humanize_tool_call

AgentStatus = Literal["running", "done", "failed"]


@dataclass(frozen=True)
class AgentRow:
    name: str
    status: AgentStatus = "running"
    attempt: int = 1
    turn: int = 0
    last_action: str | None = None
    last_action_detail: str | None = None
    duration_ms: int | None = None
    cost_usd: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class DashboardState:
    current_phase: str | None = None
    agents: dict[str, AgentRow] = field(default_factory=dict)

    @property
    def completed_count(self) -> int:
        return sum(1 for r in self.agents.values() if r.status in ("done", "failed"))

    @property
    def total_cost(self) -> float:
        return sum(r.cost_usd or 0.0 for r in self.agents.values())

    def apply(self, event: DisplayEvent) -> "DashboardState":
        """Return a new state with the event folded in (immutable)."""
        if isinstance(event, PhaseEvent):
            return replace(self, current_phase=event.phase)

        if isinstance(event, ResumeEvent):
            agents = dict(self.agents)
            for name in event.completed_agents:
                agents[name] = AgentRow(name=name, status="done", attempt=1)
            return replace(self, agents=agents)

        if isinstance(event, AgentEvent):
            agents = dict(self.agents)
            cur = agents.get(event.agent_name, AgentRow(name=event.agent_name))
            if event.event == "start":
                agents[event.agent_name] = replace(
                    cur, status="running", attempt=event.attempt, error=None)
            else:  # end
                status: AgentStatus = "done" if event.success else "failed"
                agents[event.agent_name] = replace(
                    cur, status=status,
                    duration_ms=event.duration_ms if event.duration_ms is not None else cur.duration_ms,
                    cost_usd=event.cost_usd if event.cost_usd is not None else cur.cost_usd,
                    error=event.error)
            return replace(self, agents=agents)

        if isinstance(event, ToolCallEvent):
            agents = dict(self.agents)
            cur = agents.get(event.agent_name)
            if cur is not None:
                detail = humanize_tool_call(event.tool_name, event.parameters or {})
                agents[event.agent_name] = replace(
                    cur, last_action=event.tool_name, last_action_detail=detail)
            return replace(self, agents=agents)

        if isinstance(event, LlmTurnEvent):
            agents = dict(self.agents)
            cur = agents.get(event.agent_name)
            if cur is not None:
                agents[event.agent_name] = replace(cur, turn=event.turn)
            return replace(self, agents=agents)

        # ErrorEvent, SummaryEvent, WorkflowHeader -> no dashboard-state change
        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_dashboard_state.py -q`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/dashboard_state.py packages/core/tests/display/test_dashboard_state.py
git commit -m "feat(display): DashboardState immutable state machine for live dashboard"
```

---

### Task 3: `LiveDashboardRenderer` — dispatcher Renderer + `__rich_console__` renderable

**Files:**
- Create: `packages/core/src/shannon_core/display/live_dashboard.py`
- Test: `packages/core/tests/display/test_live_dashboard.py`

Holds a `_snapshot: DashboardState` (atomic ref swap on each event) and a start monotonic clock. As a dispatcher `Renderer`, `render(event)` folds the event into a new snapshot. As a Rich renderable, `__rich_console__` builds the dashboard from the snapshot + live elapsed. `Spinner` widgets animate via Rich's Live refresh thread.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/display/test_live_dashboard.py`:
```python
import io

from rich.console import Console

from shannon_core.display.events import PhaseEvent, AgentEvent, ToolCallEvent
from shannon_core.display.live_dashboard import LiveDashboardRenderer


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, width=100, force_terminal=True, color_system=None, force_interactive=True), buf


async def test_render_folds_event_into_snapshot():
    console, _ = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start"))
    assert r.snapshot.current_phase == "recon"


async def test_rich_console_renders_phase_and_agent_rows():
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(PhaseEvent(timestamp="t", category="PHASE", phase="vulnerability-analysis", event="start"))
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="injection-vuln",
                              event="start", attempt=1))
    await r.render(ToolCallEvent(timestamp="t", category="TOOL", agent_name="injection-vuln",
                                 tool_name="Bash", parameters={"command": "rg -n eval"}))
    # Render once into the buffer
    console.print(r)
    out = buf.getvalue()
    assert "vulnerability-analysis" in out
    assert "injection-vuln" in out
    assert "Bash" in out or "command=rg -n eval" in out


async def test_done_agent_shows_checkmark_style():
    console, buf = _console()
    r = LiveDashboardRenderer(console)
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="auth-vuln", event="start", attempt=1))
    await r.render(AgentEvent(timestamp="t", category="AGENT", agent_name="auth-vuln", event="end",
                              attempt=1, duration_ms=4500, cost_usd=0.23, success=True))
    console.print(r)
    out = buf.getvalue()
    assert "auth-vuln" in out
    assert "4.5s" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_live_dashboard.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.display.live_dashboard'`.

- [ ] **Step 3: Implement `LiveDashboardRenderer`**

Create `packages/core/src/shannon_core/display/live_dashboard.py`:
```python
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
from typing import Iterable

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

    # ---- dispatcher Renderer protocol ----
    @property
    def snapshot(self) -> DashboardState:
        return self._snapshot

    async def render(self, event: DisplayEvent) -> None:
        # Atomic swap: build the new snapshot, then assign. The Live refresh
        # thread reads self._snapshot; GIL makes this assignment atomic.
        self._snapshot = self._snapshot.apply(event)

    # ---- Rich renderable (re-invoked by Live each refresh tick) ----
    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield self._render()

    def _render(self) -> Table:
        snap = self._snapshot
        elapsed = int(time.monotonic() - self._start_monotonic)

        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(width=2)   # status icon
        grid.add_column(ratio=2)   # agent
        grid.add_column(ratio=1)   # turn / time
        grid.add_column(ratio=3)   # last action

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
        else:  # failed
            icon = Text(_FAILED, style="red")
            mid = Text(format_duration(row.duration_ms or 0))

        label = Text.assemble((f"{agent_prefix(row.name)} ", "bold"), row.name)
        detail = Text(row.last_action_detail or row.last_action or "")
        line.add_row(icon, label, mid, detail)
        return line
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_live_dashboard.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/live_dashboard.py packages/core/tests/display/test_live_dashboard.py
git commit -m "feat(display): LiveDashboardRenderer stateful dashboard + __rich_console__ renderable"
```

---

## Phase 2 — Concurrency + audit bridging

### Task 4: `DisplayDispatcher` internal `asyncio.Lock`

**Files:**
- Modify: `packages/core/src/shannon_core/display/dispatcher.py`
- Test: `packages/core/tests/display/test_dispatcher.py` (append)

Without a lock, two concurrent `dispatch` calls interleave per-renderer awaits (e.g. file writes tear). Add a lock so each event renders atomically.

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/display/test_dispatcher.py`:
```python
import asyncio

from shannon_core.display.dispatcher import DisplayDispatcher
from shannon_core.display.events import PhaseEvent


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
    ev = PhaseEvent(timestamp="t", category="PHASE", phase="p", event="start")
    await asyncio.gather(d.dispatch(ev), d.dispatch(ev))
    # No start should appear before the previous end -> no interleaving
    assert log == ["start-A", "end-A", "start-A", "end-A"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_dispatcher.py::test_dispatch_serializes_concurrent_events -q`
Expected: FAIL — log is interleaved (`["start-A", "start-A", "end-A", "end-A"]`).

- [ ] **Step 3: Add the lock**

Replace the body of `packages/core/src/shannon_core/display/dispatcher.py` with:
```python
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
```

- [ ] **Step 4: Run dispatcher tests**

Run: `uv run pytest packages/core/tests/display/test_dispatcher.py -q`
Expected: all passed (including the new serialization test).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/dispatcher.py packages/core/tests/display/test_dispatcher.py
git commit -m "feat(display): serialize DisplayDispatcher.dispatch with asyncio.Lock"
```

---

### Task 5: `SessionToolAuditLogger` — bridge core `ToolAuditLogger` → `AuditSession`

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/audit/session_tool_audit_logger.py`
- Test: `packages/whitebox/tests/test_session_tool_audit_logger.py`

Implements core `ToolAuditLogger` so tool/llm/error events from `MessageDispatcher` reach the display pipeline (currently they go `ActivityToolAuditLogger` → core `ActivityLogger.info`, bypassing it).

- [ ] **Step 1: Write the failing test**

Create `packages/whitebox/tests/test_session_tool_audit_logger.py`:
```python
from pathlib import Path

from shannon_core.models.metrics import SessionMetadata
from shannon_whitebox.audit.session import AuditSession
from shannon_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
from shannon_whitebox.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="s1", web_url="https://example.com", output_path=str(tmp_path))


def _read_log(tmp_path: Path) -> str:
    return (generate_audit_path(_make_meta(tmp_path)) / "workflow.log").read_text()


async def test_tool_start_reaches_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    await session.start_agent("recon", "p", attempt=1)
    lg = SessionToolAuditLogger(session)
    await lg.log_tool_start("Read", {"file_path": "/app/main.py"})
    await session.close()
    assert "[TOOL]  recon: Read:" in _read_log(tmp_path)
    assert "file_path=/app/main.py" in _read_log(tmp_path)


async def test_assistant_turn_reaches_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    await session.start_agent("recon", "p", attempt=1)
    lg = SessionToolAuditLogger(session)
    await lg.log_assistant_turn(2, "Found sinks")
    await session.close()
    assert "[LLM]   recon: Turn 2:" in _read_log(tmp_path)


async def test_log_error_reaches_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    lg = SessionToolAuditLogger(session)
    await lg.log_error("boom", turn_count=3, duration_ms=1000)
    await session.close()
    assert "[ERROR]" in _read_log(tmp_path)
    assert "boom" in _read_log(tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/test_session_tool_audit_logger.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `SessionToolAuditLogger`**

Create `packages/whitebox/src/shannon_whitebox/audit/session_tool_audit_logger.py`:
```python
"""SessionToolAuditLogger — bridges core ToolAuditLogger events to AuditSession.

MessageDispatcher (inside provider.call) calls log_tool_start / log_tool_end /
log_error / log_assistant_turn. Routing them through AuditSession feeds the
display pipeline (WorkflowLogger -> dispatcher -> renderers), so tool/llm
events appear in workflow.log and on the live dashboard.
"""
from __future__ import annotations

from typing import Any

from shannon_core.agents.tool_audit_logger import ToolAuditLogger


class SessionToolAuditLogger(ToolAuditLogger):
    def __init__(self, session: "AuditSession") -> None:  # noqa: F821 (typed below)
        self._session = session

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        await self._session.log_event(
            "tool_start", {"toolName": tool_name, "parameters": parameters})

    async def log_tool_end(self, result: Any) -> None:
        # tool_end has no DisplayEvent surface; record for file completeness only.
        await self._session.log_event("tool_end", {"result": str(result)[:200]})

    async def log_assistant_turn(self, turn: int, content: str) -> None:
        await self._session.log_event(
            "llm_response", {"turn": turn, "content": content})

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        await self._session.log_error(
            RuntimeError(error), context=f"turn={turn_count}, {duration_ms}ms")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/whitebox/tests/test_session_tool_audit_logger.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/session_tool_audit_logger.py packages/whitebox/tests/test_session_tool_audit_logger.py
git commit -m "feat(whitebox): SessionToolAuditLogger bridges tool/llm events to AuditSession"
```

---

### Task 6: Extend `ToolAuditLogger` with `log_assistant_turn`; call it in `MessageDispatcher`

**Files:**
- Modify: `packages/core/src/shannon_core/agents/tool_audit_logger.py`
- Modify: `packages/core/src/shannon_core/agents/message_dispatcher.py`
- Test: `packages/core/tests/agents/test_message_dispatcher.py` (create if absent, else append)

Add an LLM-turn hook so reasoning text reaches the display via `SessionToolAuditLogger.log_assistant_turn`.

- [ ] **Step 1: Write the failing test**

Create or append to `packages/core/tests/agents/test_message_dispatcher.py`:
```python
from shannon_core.agents.message_dispatcher import MessageDispatcher


class _RecordingAuditLogger:
    def __init__(self) -> None:
        self.turns: list[tuple[int, str]] = []

    async def log_tool_start(self, tool_name, parameters): pass
    async def log_tool_end(self, result): pass
    async def log_error(self, error, *, turn_count=0, duration_ms=0): pass
    async def log_assistant_turn(self, turn: int, content: str) -> None:
        self.turns.append((turn, content))


class _AssistantEvent:
    type = "assistant"
    def __init__(self, text: str) -> None:
        self.content = [{"text": text}]


async def test_assistant_event_logs_turn():
    rec = _RecordingAuditLogger()
    d = MessageDispatcher(audit_logger=rec)
    await d.dispatch(_AssistantEvent("Analyzing sinks"))
    assert rec.turns == [(1, "Analyzing sinks")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/agents/test_message_dispatcher.py::test_assistant_event_logs_turn -q`
Expected: FAIL — `AttributeError: ...log_assistant_turn` (ABC doesn't define it / Null impl lacks it).

- [ ] **Step 3: Add `log_assistant_turn` to the ABC + impls**

In `packages/core/src/shannon_core/agents/tool_audit_logger.py`, add the abstract method and implement it in both `NullToolAuditLogger` and `ActivityToolAuditLogger`.

Add to `ToolAuditLogger` (after `log_error`):
```python
    @abstractmethod
    async def log_assistant_turn(self, turn: int, content: str) -> None: ...
```

Add to `NullToolAuditLogger`:
```python
    async def log_assistant_turn(self, turn: int, content: str) -> None:
        pass
```

Add to `ActivityToolAuditLogger`:
```python
    async def log_assistant_turn(self, turn: int, content: str) -> None:
        self._logger.info("assistant_turn", turn=turn, content=content[:500])
```

- [ ] **Step 4: Call it in `MessageDispatcher._handle_assistant`**

In `packages/core/src/shannon_core/agents/message_dispatcher.py`, edit `_handle_assistant` to collect the turn text and log it once. Replace the method body (lines ~69-80) with:
```python
    async def _handle_assistant(self, event: Any) -> str:
        self.turn_count += 1
        turn_text = ""
        for block in getattr(event, "content", []):
            if hasattr(block, "text"):
                text = block.text
                self.text_parts.append(text)
                turn_text += text
                if self._is_spending_cap_in_text(text):
                    self.spending_cap_detected = True
        if turn_text:
            await self.audit_logger.log_assistant_turn(self.turn_count, turn_text)
        error = getattr(event, "error", None)
        if error and self._on_error:
            self._on_error(str(error))
        return "continue"
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest packages/core/tests/agents/test_message_dispatcher.py::test_assistant_event_logs_turn -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/tool_audit_logger.py packages/core/src/shannon_core/agents/message_dispatcher.py packages/core/tests/agents/test_message_dispatcher.py
git commit -m "feat(agents): log_assistant_turn hook surfaces LLM turns to audit loggers"
```

---

## Phase 3 — Wire display config through WorkflowLogger / AuditSession

### Task 7: `WorkflowLogger` — `console`/`dashboard` params, attach `LiveDashboardRenderer`

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py`
- Modify: `packages/whitebox/tests/test_workflow_logger.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `packages/whitebox/tests/test_workflow_logger.py`:
```python
async def test_use_rich_attaches_dashboard_renderer(tmp_path: Path):
    import io
    from rich.console import Console
    from shannon_core.display.live_dashboard import LiveDashboardRenderer
    meta = _make_meta(tmp_path)
    console = Console(file=io.StringIO(), width=100)
    dashboard = LiveDashboardRenderer(console)
    logger = WorkflowLogger(meta, use_rich=True, console=console, dashboard=dashboard)
    await logger.initialize(workflow_id="wf-1")
    # dispatcher should have 3 renderers: File, RichConsole, LiveDashboard
    assert len(logger._dispatcher._renderers) == 3
    await logger.log_phase("recon", "start")
    await logger.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/test_workflow_logger.py::test_use_rich_attaches_dashboard_renderer -q`
Expected: FAIL — `TypeError: unexpected keyword argument 'console'`.

- [ ] **Step 3: Update `WorkflowLogger`**

In `packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py`, replace the `__init__` and `initialize` methods with:
```python
    def __init__(self, session_metadata: SessionMetadata, use_rich: bool = False,
                 console=None, dashboard=None) -> None:
        self._meta = session_metadata
        self._workflow_id: str | None = None
        self._stream: LogStream | None = None
        self._dispatcher: DisplayDispatcher | None = None
        self._use_rich = use_rich
        self._console = console
        self._dashboard = dashboard

    async def initialize(self, workflow_id: str | None = None) -> None:
        self._workflow_id = workflow_id
        path = generate_workflow_log_path(self._meta)
        self._stream = LogStream(path)
        await self._stream.open()

        renderers: list = [FileLogRenderer(self._stream)]
        if self._use_rich:
            from shannon_core.display.rich_renderer import RichConsoleRenderer
            renderers.append(RichConsoleRenderer(self._console))
            if self._dashboard is not None:
                renderers.append(self._dashboard)
        self._dispatcher = DisplayDispatcher(renderers)

        await self._dispatcher.dispatch(WorkflowHeader(
            timestamp=format_log_time(), category="HEADER",
            workflow_id=workflow_id, target_url=self._meta.web_url,
        ))
```

Leave all other methods unchanged.

- [ ] **Step 4: Run the workflow_logger suite**

Run: `uv run pytest packages/whitebox/tests/test_workflow_logger.py -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py packages/whitebox/tests/test_workflow_logger.py
git commit -m "feat(whitebox): WorkflowLogger accepts console/dashboard, attaches LiveDashboardRenderer"
```

---

### Task 8: `AuditSession` — display config passthrough

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/audit/session.py`
- Modify: `packages/whitebox/tests/test_audit_session.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `packages/whitebox/tests/test_audit_session.py`:
```python
async def test_display_config_passed_to_workflow_logger(tmp_path: Path):
    import io
    from rich.console import Console
    from shannon_core.display.live_dashboard import LiveDashboardRenderer
    meta = _make_meta(tmp_path)
    console = Console(file=io.StringIO(), width=100)
    dashboard = LiveDashboardRenderer(console)
    session = AuditSession(meta, use_rich=True, console=console, dashboard=dashboard)
    await session.initialize(workflow_id="wf-1")
    assert session._workflow_logger._use_rich is True
    assert len(session._workflow_logger._dispatcher._renderers) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/test_audit_session.py::test_display_config_passed_to_workflow_logger -q`
Expected: FAIL — `TypeError: AuditSession.__init__() got an unexpected keyword argument 'use_rich'`.

- [ ] **Step 3: Update `AuditSession.__init__` and `initialize`**

In `packages/whitebox/src/shannon_whitebox/audit/session.py`, replace `__init__` and the `WorkflowLogger(self._meta)` line in `initialize`:

```python
    def __init__(self, session_metadata: SessionMetadata, use_rich: bool = False,
                 console=None, dashboard=None):
        self._meta = session_metadata
        self._use_rich = use_rich
        self._console = console
        self._dashboard = dashboard
        self._agent_logger: AgentLogger | None = None
        self._workflow_logger: WorkflowLogger | None = None
        self._metrics_tracker: MetricsTracker | None = None
        self._lock = asyncio.Lock()
        self._current_agent_name: str | None = None
```

And in `initialize`, replace:
```python
        self._workflow_logger = WorkflowLogger(self._meta)
```
with:
```python
        self._workflow_logger = WorkflowLogger(
            self._meta, use_rich=self._use_rich,
            console=self._console, dashboard=self._dashboard)
```

- [ ] **Step 4: Run the audit_session suite**

Run: `uv run pytest packages/whitebox/tests/test_audit_session.py -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/session.py packages/whitebox/tests/test_audit_session.py
git commit -m "feat(whitebox): AuditSession accepts display config, passes to WorkflowLogger"
```

---

## Phase 4 — Worker-level singleton + driver

### Task 9: `get_audit_session` singleton registry + `NullAuditSession`

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/audit/session_registry.py`
- Test: `packages/whitebox/tests/test_session_registry.py`

- [ ] **Step 1: Write the failing test**

Create `packages/whitebox/tests/test_session_registry.py`:
```python
from shannon_whitebox.audit.session_registry import (
    get_audit_session, set_audit_session, clear_audit_session, NullAuditSession,
)


async def test_default_is_null_and_safe():
    clear_audit_session()
    s = get_audit_session()
    assert isinstance(s, NullAuditSession)
    # All methods are no-ops and safe to call without initialize()
    await s.start_agent("recon", "p", attempt=1)
    await s.log_event("tool_start", {"toolName": "Read"})
    await s.log_phase_start("recon")
    await s.end_agent("recon", None)


async def test_set_then_get_returns_instance():
    clear_audit_session()
    sentinel = object()
    set_audit_session(sentinel)  # type: ignore[arg-type]
    assert get_audit_session() is sentinel
    clear_audit_session()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/test_session_registry.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the registry**

Create `packages/whitebox/src/shannon_whitebox/audit/session_registry.py`:
```python
"""Process-wide registry for the current scan's AuditSession.

Activities reach the live display via get_audit_session(). The driver
(run_scan) sets the singleton before starting the worker and clears it after.

NullAuditSession is the safe default when no scan is active (tests, standalone
tooling): every method is a no-op so callers never null-check.
"""
from __future__ import annotations

from typing import Any

_current: "Any" = None


class NullAuditSession:
    """No-op AuditSession stand-in. Safe to call without initialize()."""

    async def initialize(self, workflow_id: str | None = None) -> None: pass
    async def start_agent(self, agent_name: str, prompt: str, attempt: int = 1) -> None: pass
    async def end_agent(self, agent_name: str, result: Any) -> None: pass
    async def log_event(self, event_type: str, event_data: Any) -> None: pass
    async def log_phase_start(self, phase: str) -> None: pass
    async def log_phase_complete(self, phase: str) -> None: pass
    async def log_workflow_complete(self, summary: Any) -> None: pass
    async def log_error(self, error: Any, context: str | None = None) -> None: pass
    async def log_resume_header(self, resume_info: Any) -> None: pass
    async def update_session_status(self, status: str) -> None: pass
    async def close(self) -> None: pass


def set_audit_session(session: Any) -> None:
    global _current
    _current = session


def get_audit_session() -> Any:
    return _current if _current is not None else NullAuditSession()


def clear_audit_session() -> None:
    global _current
    _current = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/whitebox/tests/test_session_registry.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/session_registry.py packages/whitebox/tests/test_session_registry.py
git commit -m "feat(whitebox): audit session registry + NullAuditSession for activities"
```

---

### Task 10: `run_scan` driver — Console+Live lifecycle, register singleton, delete ticker

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`

This is the core integration: build the display, register it, run the scan inside `Live`, and **delete `poll_workflow_progress`** (the `print()` heartbeat).

- [ ] **Step 1: Read the current `worker.py` to confirm structure**

Run: `sed -n '1,135p' packages/whitebox/src/shannon_whitebox/worker.py`
Expected: shows `poll_workflow_progress` (lines 35-47), `run_scan` (50-129) with `poll_task = asyncio.create_task(poll_workflow_progress(handle))` at line 98.

- [ ] **Step 2: Replace `worker.py` with the wired version**

Replace the entire contents of `packages/whitebox/src/shannon_whitebox/worker.py` with:
```python
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from .pipeline.activities import (
    render_findings,
    run_agent,
    run_auth_validation,
    run_code_index,
    run_credential_check,
    run_merge_sink_reports,
    run_entry_point_fusion,
    run_preflight,
    run_render_dataflow_hints,
    run_risk_scoring,
    run_save_adjudication,
    run_vuln_agent,
    run_attack_chain_assembly,
    run_framework_analysis,
    run_frontend_mapping,
    run_route_chain_building,
    log_phase_start_activity,
    log_phase_complete_activity,
)
from .pipeline.workflows import WhiteboxScanWorkflow
from .pipeline.shared import PipelineInput, PipelineProgress
from shannon_core.utils.paths import resolve_workspaces_dir
from shannon_core.services.temporal_infra import generate_task_queue

TASK_QUEUE_PREFIX = "shannon-py-wb"


async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233",
                   use_rich: bool = False) -> dict:
    import sys
    from rich.console import Console
    from rich.live import Live
    from shannon_core.session import SessionManager
    from shannon_core.models.metrics import SessionMetadata
    from shannon_whitebox.audit.session import AuditSession
    from shannon_whitebox.audit.session_registry import (
        set_audit_session, clear_audit_session,
    )
    from shannon_whitebox.audit.display_lifecycle import run_with_display

    # Persist session data so blackbox can discover repo_path
    if input.workspace_name:
        workspaces_dir = resolve_workspaces_dir(input.repo_path)
        mgr = SessionManager(workspaces_dir)
        mgr.create_workspace(
            web_url=input.web_url or "",
            repo_path=input.repo_path,
            name=input.workspace_name,
        )

    client = await Client.connect(temporal_address)
    task_queue = generate_task_queue(TASK_QUEUE_PREFIX)

    worker = Worker(
        client=client,
        task_queue=task_queue,
        workflows=[WhiteboxScanWorkflow],
        activities=[
            render_findings, run_agent, run_auth_validation, run_code_index,
            run_credential_check, run_merge_sink_reports, run_entry_point_fusion,
            run_preflight, run_render_dataflow_hints, run_risk_scoring,
            run_save_adjudication, run_vuln_agent, run_attack_chain_assembly,
            run_framework_analysis, run_frontend_mapping, run_route_chain_building,
            log_phase_start_activity, log_phase_complete_activity,
        ],
    )

    meta = SessionMetadata(
        id=input.workspace_name or "whitebox-scan",
        web_url=input.web_url,
        repo_path=input.repo_path,
        output_path=str(resolve_workspaces_dir(input.repo_path)),
    )

    async with worker:
        async with run_with_display(meta, use_rich=use_rich) as session:
            set_audit_session(session)
            handle = await client.start_workflow(
                WhiteboxScanWorkflow.run,
                input,
                id=input.workspace_name or f"whitebox-{int(asyncio.get_event_loop().time())}",
                task_queue=task_queue,
            )
            try:
                result = await handle.result()
            finally:
                clear_audit_session()

            result_dict = asdict(result) if not isinstance(result, dict) else dict(result)
            result_dict["workspace_name"] = input.workspace_name
            result_dict["web_url"] = input.web_url

            workspaces_dir = resolve_workspaces_dir(input.repo_path)
            if input.workspace_name:
                result_dict["deliverables_path"] = str(
                    workspaces_dir / input.workspace_name / input.deliverables_subdir)
            else:
                result_dict["deliverables_path"] = str(
                    Path(input.repo_path) / input.deliverables_subdir)
            return result_dict


def main():
    import sys
    asyncio.run(run_scan(PipelineInput(repo_path=sys.argv[1] if len(sys.argv) > 1 else ".")))
```

> Note: `poll_workflow_progress` and its `print()` are gone. Progress is event-driven via the dashboard.

- [ ] **Step 3: Create the display lifecycle helper**

Create `packages/whitebox/src/shannon_whitebox/audit/display_lifecycle.py`:
```python
"""Display lifecycle: construct AuditSession + shared Console/Live and yield
the session inside an active Live context (rich mode) or plain (non-rich)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from shannon_core.models.metrics import SessionMetadata

from .session import AuditSession


@asynccontextmanager
async def run_with_display(meta: SessionMetadata, use_rich: bool = False) -> AsyncIterator[AuditSession]:
    if use_rich:
        from rich.console import Console
        from rich.live import Live
        from shannon_core.display.live_dashboard import LiveDashboardRenderer

        console = Console()
        dashboard = LiveDashboardRenderer(console)
        session = AuditSession(meta, use_rich=True, console=console, dashboard=dashboard)
        await session.initialize(workflow_id=meta.id)
        live = Live(dashboard, console=console, transient=False, refresh_per_second=10)
        try:
            with live:
                yield session
        finally:
            await session.close()
    else:
        session = AuditSession(meta, use_rich=False)
        await session.initialize(workflow_id=meta.id)
        try:
            yield session
        finally:
            await session.close()
```

- [ ] **Step 4: Verify import sanity**

Run: `uv run python -c "from shannon_whitebox.worker import run_scan; print('ok')"`
Expected: prints `ok` (imports resolve; `log_phase_start_activity`/`log_phase_complete_activity` added in Task 13).

> If this errors on the phase-marker imports, that is expected until Task 13 — proceed but mark this step as depending on Task 13. To keep tasks independently committable, swap the import order: do Task 13 (phase markers) before this task if executing sequentially. The plan presents Task 10 here for narrative flow; executor may reorder 10↔13.

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py packages/whitebox/src/shannon_whitebox/audit/display_lifecycle.py
git commit -m "feat(whitebox): run_scan wires AuditSession+Live, registers singleton, drops print() poller"
```

---

### Task 11: CLI `start` — `--plain` flag + `use_rich` autodetect

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py`

- [ ] **Step 1: Add the flag and thread `use_rich`**

In `packages/whitebox/src/shannon_whitebox/cli/main.py`, add the option to the `start` command's decorator block (after the `--temporal-address` option, line ~33):
```python
@click.option("--plain", is_flag=True, help="Disable Rich live dashboard; print one line per event (CI/pipes).")
```

Add `plain` to the `start` signature:
```python
def start(repo, output, workspace, config_path, pipeline_testing, temporal_address, plain):
```

Replace the `result = asyncio.run(run_scan(input, temporal_address))` line with:
```python
    import sys
    use_rich = sys.stdout.isatty() and not plain
    result = asyncio.run(run_scan(input, temporal_address, use_rich=use_rich))
```

- [ ] **Step 2: Smoke-check the CLI parses**

Run: `uv run shannon-whitebox start --help`
Expected: help text includes `--plain`.

- [ ] **Step 3: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py
git commit -m "feat(whitebox): --plain flag + TTY autodetect for Rich display"
```

---

## Phase 5 — Activity / workflow wiring (the heart)

### Task 12: Wire agent activities to `AuditSession`

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` (`run_agent`)
- Modify: `packages/core/src/shannon_core/agents/executor.py` (thread `tool_audit_logger`)
- Modify: `packages/core/src/shannon_core/agents/runner.py` (prefer passed `tool_audit_logger`)
- Test: `packages/whitebox/tests/test_activity_display_wiring.py` (L3)

Route agent start/end/tool/llm/error events into the display pipeline.

- [ ] **Step 1: Thread `tool_audit_logger` through `run_claude_prompt`**

In `packages/core/src/shannon_core/agents/runner.py`, add a param and prefer it. Change the signature (line ~89) to add `tool_audit_logger=None`:
```python
async def run_claude_prompt(
    prompt: str,
    repo_path: str,
    model_tier: str = "medium",
    output_format: dict | None = None,
    structured_output_schema: dict | None = None,
    api_key: str | None = None,
    deliverables_subdir: str | None = None,
    provider_config: dict | None = None,
    audit_logger: "ActivityLogger | None" = None,
    tool_audit_logger=None,
) -> ClaudeRunResult:
```

Replace lines ~134-136 (the `ActivityToolAuditLogger` wrapping) with:
```python
        from .tool_audit_logger import ActivityToolAuditLogger
        if tool_audit_logger is not None:
            active_tool_logger = tool_audit_logger
        elif audit_logger is not None:
            active_tool_logger = ActivityToolAuditLogger(audit_logger)
        else:
            active_tool_logger = None
```

And change the `provider.call(...)` call (line ~138) to pass `audit_logger=active_tool_logger`.

- [ ] **Step 2: Thread `tool_audit_logger` through `AgentExecutor.execute`**

In `packages/core/src/shannon_core/agents/executor.py`, add the param and pass it through. In `execute`'s signature (line ~25), add after `audit_logger`:
```python
        tool_audit_logger=None,
```

In the `run_claude_prompt(...)` call (line ~65), add `tool_audit_logger=tool_audit_logger,` alongside `audit_logger=audit_logger`.

- [ ] **Step 3: Wire `run_agent` to `AuditSession`**

In `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`, replace the body of `run_agent` (lines 75-99) with:
```python
@activity.defn
async def run_agent(input: ActivityInput) -> dict:
    from shannon_whitebox.audit.session_registry import get_audit_session
    from shannon_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
    from shannon_core.models.audit import AgentEndResult

    agent_name = AgentName(input.workspace_name)
    attempt = activity.info().attempt
    session = get_audit_session()
    tool_audit_logger = SessionToolAuditLogger(session)
    try:
        repo, deliverables, _ = _get_paths(input)
        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)

        await session.start_agent(agent_name.value, f"agent={agent_name.value}", attempt=attempt)
        metrics = await executor.execute(
            agent_name=agent_name,
            repo_path=str(repo),
            web_url=input.web_url,
            deliverables_path=str(deliverables),
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
            prompt_override=input.prompt_override,
            audit_logger=create_activity_logger(),
            tool_audit_logger=tool_audit_logger,
        )
        await session.end_agent(agent_name.value, AgentEndResult(
            success=True,
            duration_ms=metrics.duration_ms,
            cost_usd=metrics.cost_usd or 0.0,
            attempt_number=attempt,
            model=metrics.model,
        ))
        return metrics.model_dump()
    except PentestError as e:
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=0, cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=0, cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

- [ ] **Step 4: Write the L3 wiring test**

Create `packages/whitebox/tests/test_activity_display_wiring.py`:
```python
from pathlib import Path

from shannon_core.models.metrics import SessionMetadata
from shannon_whitebox.audit.session import AuditSession
from shannon_whitebox.audit.session_registry import set_audit_session, clear_audit_session
from shannon_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
from shannon_whitebox.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="s1", web_url="https://example.com", output_path=str(tmp_path))


async def test_session_tool_audit_logger_feeds_workflow_log(tmp_path: Path):
    """L3: tool/llm events through SessionToolAuditLogger reach workflow.log
    via AuditSession -> WorkflowLogger -> dispatcher -> FileLogRenderer."""
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    set_audit_session(session)
    try:
        await session.start_agent("injection-vuln", "p", attempt=1)
        lg = SessionToolAuditLogger(session)
        await lg.log_tool_start("Bash", {"command": "rg -n eval"})
        await lg.log_assistant_turn(1, "found sinks")
        from shannon_core.models.audit import AgentEndResult
        await session.end_agent("injection-vuln", AgentEndResult(
            success=True, duration_ms=100, cost_usd=0.01, attempt_number=1))
    finally:
        clear_audit_session()
        await session.close()
    wf = (generate_audit_path(_make_meta(tmp_path)) / "workflow.log").read_text()
    assert "[AGENT] [Injection] injection-vuln: Starting" in wf
    assert "[TOOL]  [Injection] injection-vuln: Bash:" in wf
    assert "[LLM]   [Injection] injection-vuln: Turn 1:" in wf
    assert "[AGENT] [Injection] injection-vuln: Completed" in wf
```

- [ ] **Step 5: Run the L3 test**

Run: `uv run pytest packages/whitebox/tests/test_activity_display_wiring.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/runner.py packages/core/src/shannon_core/agents/executor.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_activity_display_wiring.py
git commit -m "feat(whitebox): wire run_agent to AuditSession (start/end/tool/llm/error)"
```

---

### Task 13: Phase-marker activities + workflow scheduling

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

- [ ] **Step 1: Add phase-marker activities**

In `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`, add after `run_vuln_agent` (line ~103):
```python
@activity.defn
async def log_phase_start_activity(input: ActivityInput) -> None:
    from shannon_whitebox.audit.session_registry import get_audit_session
    phase = input.workspace_name or "unknown"
    await get_audit_session().log_phase_start(phase)


@activity.defn
async def log_phase_complete_activity(input: ActivityInput) -> None:
    from shannon_whitebox.audit.session_registry import get_audit_session
    phase = input.workspace_name or "unknown"
    await get_audit_session().log_phase_complete(phase)
```

> The workflow passes the phase name via `workspace_name` on a throwaway `ActivityInput`.

- [ ] **Step 2: Schedule markers at phase boundaries in the workflow**

In `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`, insert marker activities at each phase. After the `act_input` is built (line ~52), add a helper call before each phase. Concretely, insert these calls:

Before `self._state.current_phase = "pre-recon"` (line ~111), insert:
```python
            await workflow.execute_activity(
                activities.log_phase_start_activity,
                ActivityInput(**{**act_input.__dict__, "workspace_name": "pre-recon"}),
                start_to_close_timeout=timedelta(seconds=10),
            )
```

Before `self._state.current_phase = "recon"` (line ~179), insert the same with `"workspace_name": "recon"`.

Before `self._state.current_phase = "vulnerability-analysis"` (line ~203), insert with `"workspace_name": "vulnerability-analysis"`.

Before `self._state.current_agent = "render-findings"` (line ~248), insert with `"workspace_name": "reporting"`.

(Insert the `ActivityInput(**{**act_input.__dict__, "workspace_name": ...})` form exactly as used elsewhere in the file.)

- [ ] **Step 3: Verify workflow imports/syntax**

Run: `uv run python -c "from shannon_whitebox.pipeline.workflows import WhiteboxScanWorkflow; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
git commit -m "feat(whitebox): phase-marker activities emit PhaseEvents at workflow boundaries"
```

---

## Phase 6 — Integration gate + acceptance

### Task 14: L2 display integration gate (the anti-repetition check)

**Files:**
- Create: `packages/whitebox/tests/test_display_integration.py`

Drive a scripted event sequence **through `AuditSession`** (real WorkflowLogger→dispatcher→renderers path) and assert BOTH a scrolling-log line AND a dashboard region appear in the captured terminal. This is the check the prior plan lacked — if `AuditSession` isn't wired to renderers, the capture is empty.

- [ ] **Step 1: Write the gate test**

Create `packages/whitebox/tests/test_display_integration.py`:
```python
"""L2 gate: AuditSession -> WorkflowLogger -> dispatcher -> renderers actually
reaches the terminal. If this is empty, the pipeline is not wired (the failure
mode of the prior logging-display-optimization plan)."""
import io
from pathlib import Path

from rich.console import Console

from shannon_core.models.audit import AgentEndResult, WorkflowSummary, AgentMetricsSummary
from shannon_core.models.metrics import SessionMetadata
from shannon_core.display.live_dashboard import LiveDashboardRenderer
from shannon_whitebox.audit.session import AuditSession


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="gate", web_url="https://example.com", output_path=str(tmp_path))


async def test_audit_session_reaches_console_and_dashboard(tmp_path: Path):
    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=True, color_system=None, force_interactive=True)
    dashboard = LiveDashboardRenderer(console)
    session = AuditSession(_make_meta(tmp_path), use_rich=True, console=console, dashboard=dashboard)
    await session.initialize(workflow_id="wf-gate")

    await session.log_phase_start("vulnerability-analysis")
    await session.start_agent("injection-vuln", "p", attempt=1)
    await session.log_event("tool_start", {"toolName": "Bash", "parameters": {"command": "rg -n eval"}})
    await session.log_event("llm_response", {"turn": 2, "content": "found sinks"})
    await session.end_agent("injection-vuln", AgentEndResult(
        success=True, duration_ms=5200, cost_usd=0.15, attempt_number=1))

    # Render the dashboard once into the same buffer
    console.print(dashboard)
    await session.close()

    out = buf.getvalue()
    # Scrolling-log lines (RichConsoleRenderer printed each event)
    assert "[AGENT]" in out and "injection-vuln" in out
    assert "[TOOL]" in out
    assert "[LLM]" in out
    # Dashboard region (phase + agent row + totals)
    assert "vulnerability-analysis" in out
    assert "1 done" in out  # completed_count
```

- [ ] **Step 2: Run the gate test**

Run: `uv run pytest packages/whitebox/tests/test_display_integration.py -q`
Expected: PASS. (If it FAILS with empty/missing output, the wiring is broken — do not proceed; re-check Tasks 7-8, 12.)

- [ ] **Step 3: Commit**

```bash
git add packages/whitebox/tests/test_display_integration.py
git commit -m "test(whitebox): L2 display integration gate — AuditSession reaches console+dashboard"
```

---

### Task 15: DoD verification + manual smoke

**Files:** none (verification only)

Run the full suite, then verify each Definition-of-Done item from the spec is true.

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest packages/core/tests/display packages/core/tests/errors packages/whitebox/tests -q`
Expected: all passed (including the previously-failing `test_audit_session.py`, now green).

- [ ] **Step 2: DoD — `AuditSession` has a non-test caller**

Run: `grep -rn "AuditSession(" packages/whitebox/src`
Expected: at least one hit in `worker.py`/`display_lifecycle.py` (production), not only tests.

- [ ] **Step 3: DoD — activities call `get_audit_session()`**

Run: `grep -rn "get_audit_session()" packages/whitebox/src`
Expected: hits in `activities.py` (`run_agent`, `log_phase_start_activity`, `log_phase_complete_activity`).

- [ ] **Step 4: DoD — `print()` poller is gone**

Run: `grep -n "poll_workflow_progress\|Completed: {completed}" packages/whitebox/src/shannon_whitebox/worker.py`
Expected: no matches.

- [ ] **Step 5: DoD — `--plain` flag exists**

Run: `uv run shannon-whitebox start --help | grep -- --plain`
Expected: one line showing `--plain`.

- [ ] **Step 6: Manual smoke (human sign-off)**

Run in a real terminal:
```
uv run shannon-whitebox start -r /Users/mango/project/vuln-range/NodeGoat --pipeline-testing
```
Expected: a scrolling event log (phase headers, `[AGENT]`/`[TOOL]`/`[LLM]` lines) with a live dashboard pinned at the bottom (phase, N done, elapsed, $, per-agent rows with spinner). Then verify:
```
uv run shannon-whitebox start -r /Users/mango/project/vuln-range/NodeGoat --pipeline-testing --plain
```
Expected: one plain text line per event, no dashboard, no ANSI.
```
uv run shannon-whitebox start -r /Users/mango/project/vuln-range/NodeGoat --pipeline-testing | cat
```
Expected: auto-degraded plain output (piped, non-TTY), no ANSI garbage.

- [ ] **Step 7: Final commit (DoD doc update if needed)**

If the gap analysis needs updating to reflect "接入完成", update `docs/gap/logging-display-gap-analysis.md` §3.4 status and commit:
```bash
git add docs/gap/logging-display-gap-analysis.md
git commit -m "docs(gap): mark §3.4 display pipeline integration complete"
```

---

## Self-Review

**Spec coverage** (each spec section → task):
- §3 constraints → baked into Tasks 2-4, 9-10 (verified facts header).
- §4 architecture/topology → Tasks 9, 10, 13 (singleton, driver, markers).
- §5 components: `LiveDashboardRenderer`→T3, `DashboardState`→T2, `SessionToolAuditLogger`→T5, `get_audit_session`→T9, phase markers→T13, `WorkflowLogger`→T7, `AuditSession`→T8, `run_scan`→T10, CLI→T11, activities→T12. ✓
- §6 data flow + concurrency (lock, immutable snapshots) → T4 (lock), T2/T3 (immutable + atomic swap). ✓
- §7 `--plain`/degradation → T10/T11. Error/retry → T12 (`log_error`, attempt). Resume → T2 (`ResumeEvent` seeding). Summary dedupe → handled by existing `SummaryEvent` rendering (Rich mode shows Table; the CLI `click.echo` block in `main.py` remains for plain mode — acceptable per spec §7.6).
- §8 testing: L1→T2/T3/T5/T6/T9, L2 gate→T14, L3→T12, DoD→T15. ✓

**Placeholder scan:** no TBD/TODO/"fill in". Task 10 Step 4 has an explicit execution-order note (10↔13 may reorder) — that is guidance, not a placeholder. Task 13 Step 2 references an `ActivityInput(**{**act_input.__dict__, ...})` form that already exists in the file (lines 114-116), so it is concrete.

**Type consistency:** `DashboardState.apply(event)` (T2) consumed by `LiveDashboardRenderer.render` (T3). `LiveDashboardRenderer(console)` (T3) constructed identically in T7 test, T8 test, T10 `display_lifecycle`, T14. `SessionToolAuditLogger(session)` (T5) used in T12. `get_audit_session()`/`set_audit_session()`/`clear_audit_session()`/`NullAuditSession` (T9) used in T10, T12, T13. `log_assistant_turn` (T6 ABC) implemented in `SessionToolAuditLogger` (T5) and called in `MessageDispatcher` (T6). `tool_audit_logger` param threaded T12→executor→runner (T12 Steps 1-2). `AgentEndResult` fields match `models/audit.py` (verified). `AgentMetrics.duration_ms`/`cost_usd`/`model` match `models/metrics.py` (verified).

**Note on execution order:** Task 10 imports the phase-marker activities created in Task 13. If executing via subagent-driven-development, sequence Task 13 before Task 10, or split Task 10's commit. The narrative order (10 then 13) is for readability.
