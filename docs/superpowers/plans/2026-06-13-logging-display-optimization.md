# Logging Display Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a unified rendering layer (event model + dual renderers) that makes Shannon-py's logging display surpass the original Shannon, fixing all display gaps from `docs/gap/logging-display-gap-analysis.md`.

**Architecture:** A `DisplayEvent` dataclass family is the single source of truth. A `DisplayDispatcher` fans events out to two renderers: `RichConsoleRenderer` (live stdout with Panel/Progress/color) and `FileLogRenderer` (plain-text `workflow.log`, backward-compatible with the `COMPLETION_PATTERN` tail logic). Existing `ActivityLogger`/`LogStream`/`LogFileHandler` are untouched.

**Tech Stack:** Python 3.12+, Rich (terminal), pytest + pytest-asyncio (asyncio_mode=auto), uv workspace (packages/core depends on nothing new except Rich; whitebox depends on core).

**Spec:** `docs/superpowers/specs/2026-06-13-logging-display-optimization-design.md`

**Key constraints discovered:**
- `format_duration`/`format_log_time`/`format_timestamp` currently live in `shannon_whitebox.audit.utils`, but renderers are being placed in `shannon_core.display`. Core must NOT import whitebox (would be a reverse dependency). Solution: migrate these 3 helpers to `shannon_core.display.formatters`, and have whitebox re-import them to stay backward-compatible.
- `LogStream` (whitebox) is async; renderers (core) need it. Solution: define a `LineWriter` Protocol in core; `LogStream` satisfies it structurally and is injected — no import of whitebox from core.
- Error classification has TWO functions with OPPOSITE fallback semantics — `classify_for_temporal` falls back to retryable, `is_retryable_for_display` falls back to NOT retryable. Both must be ported, never merged.

---

## File Structure

**Created (core):**
- `packages/core/src/shannon_core/display/__init__.py` — package marker
- `packages/core/src/shannon_core/display/types.py` — `Renderer` + `LineWriter` Protocols
- `packages/core/src/shannon_core/display/events.py` — `DisplayEvent` dataclass family (8 types)
- `packages/core/src/shannon_core/display/formatters.py` — migrated `format_*` + new `agent_prefix`/`humanize_tool_call`/`summarize_todo`/`maybe_browser_action`/`format_error_block`
- `packages/core/src/shannon_core/display/file_renderer.py` — `FileLogRenderer`
- `packages/core/src/shannon_core/display/rich_renderer.py` — `RichConsoleRenderer`
- `packages/core/src/shannon_core/display/dispatcher.py` — `DisplayDispatcher`
- `packages/core/src/shannon_core/errors/__init__.py` — package marker
- `packages/core/src/shannon_core/errors/classification.py` — `ErrorType` + `classify_for_temporal` + `is_retryable_for_display`

**Created (tests):**
- `packages/core/tests/display/test_events.py`
- `packages/core/tests/display/test_formatters.py`
- `packages/core/tests/display/test_file_renderer.py`
- `packages/core/tests/display/test_rich_renderer.py`
- `packages/core/tests/display/test_dispatcher.py`
- `packages/core/tests/errors/test_classification.py`

**Modified:**
- `packages/core/pyproject.toml` — add `rich` dependency
- `packages/whitebox/src/shannon_whitebox/audit/utils.py` — re-import `format_*` from core
- `packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py` — emit events instead of writing LogStream directly

---

## Phase 1 — Event Model + Dispatcher Skeleton

### Task 1: Add Rich dependency to core

**Files:**
- Modify: `packages/core/pyproject.toml`

- [ ] **Step 1: Read current core dependencies**

Run: `grep -n "dependencies" packages/core/pyproject.toml`
Expected: see the `dependencies = [...]` list (currently empty or minimal).

- [ ] **Step 2: Add rich dependency**

In `packages/core/pyproject.toml`, add `rich` to the `dependencies` list:

```toml
dependencies = [
    "rich>=13.7.0",
]
```

(Keep any existing entries; append `rich>=13.7.0` to the list.)

- [ ] **Step 3: Sync workspace**

Run: `uv sync`
Expected: Rich installs; lockfile updates. No errors.

- [ ] **Step 4: Verify import works**

Run: `uv run python -c "import rich; print(rich.__version__)"`
Expected: prints a version `>=13.7.0`.

- [ ] **Step 5: Commit**

```bash
git add packages/core/pyproject.toml uv.lock
git commit -m "deps(core): add rich for terminal rendering"
```

---

### Task 2: Create display package skeleton + Protocols

**Files:**
- Create: `packages/core/src/shannon_core/display/__init__.py`
- Create: `packages/core/src/shannon_core/display/types.py`

- [ ] **Step 1: Create empty package marker**

Create `packages/core/src/shannon_core/display/__init__.py` with content:

```python
"""Display rendering layer: event model + dual renderers."""
```

- [ ] **Step 2: Write the failing test for Protocols**

Create `packages/core/tests/display/test_types.py`:

```python
from shannon_core.display.types import LineWriter, Renderer


def test_protocols_importable():
    # Protocols exist and are usable as types
    assert LineWriter is not None
    assert Renderer is not None


def test_linewriter_satisfied_by_object_with_async_write():
    # Structural typing: any object with async write(str) satisfies LineWriter
    class FakeStream:
        async def write(self, text: str) -> None:
            self.last = text

    stream = FakeStream()
    # Runtime check passes because the attribute exists
    assert hasattr(stream, "write")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.display.types'`.

- [ ] **Step 4: Write minimal implementation**

Create `packages/core/src/shannon_core/display/types.py`:

```python
"""Protocols decoupling renderers from concrete output targets."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    # Avoid a runtime import of events so this module is independently testable
    # before events.py exists.
    from shannon_core.display.events import DisplayEvent


@runtime_checkable
class LineWriter(Protocol):
    """Append-only async text sink. Satisfied structurally by LogStream."""

    async def write(self, text: str) -> None: ...


@runtime_checkable
class Renderer(Protocol):
    """Render a single DisplayEvent to some output."""

    async def render(self, event: DisplayEvent) -> None: ...
```

(`from __future__ import annotations` makes the `DisplayEvent` annotation a string at runtime, so no import error even before `events.py` exists.)

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_types.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/display/__init__.py packages/core/src/shannon_core/display/types.py packages/core/tests/display/test_types.py
git commit -m "feat(display): add package skeleton with Renderer/LineWriter protocols"
```

---

### Task 3: Event model — DisplayEvent family

**Files:**
- Create: `packages/core/src/shannon_core/display/events.py`
- Test: `packages/core/tests/display/test_events.py`

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/display/test_events.py`:

```python
import dataclasses

import pytest

from shannon_core.display.events import (
    AgentEvent,
    DisplayEvent,
    ErrorEvent,
    LlmTurnEvent,
    PhaseEvent,
    ResumeEvent,
    SummaryEvent,
    ToolCallEvent,
    WorkflowHeader,
)


def test_workflow_header_fields():
    e = WorkflowHeader(timestamp="2026-01-01 00:00:00", category="HEADER",
                       workflow_id="wf-1", target_url="https://x.com")
    assert e.workflow_id == "wf-1"
    assert e.target_url == "https://x.com"


def test_phase_event_literal():
    e = PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start")
    assert e.event == "start"


def test_agent_event_defaults():
    e = AgentEvent(timestamp="t", category="AGENT", agent_name="xss-vuln",
                   event="start", attempt=1)
    assert e.duration_ms is None
    assert e.cost_usd is None
    assert e.success is None


def test_tool_call_event():
    e = ToolCallEvent(timestamp="t", category="TOOL", agent_name="a",
                      tool_name="Bash", parameters={"command": "ls"})
    assert e.parameters == {"command": "ls"}


def test_error_event_has_classification_fields():
    e = ErrorEvent(timestamp="t", category="ERROR", error_type="ValueError",
                   message="boom")
    assert e.classified is None
    assert e.display_retryable is None


def test_summary_event():
    e = SummaryEvent(timestamp="t", category="SUMMARY", status="completed",
                     total_duration_ms=1000, total_cost_usd=0.5, agents=[])
    assert e.status == "completed"


def test_resume_event():
    e = ResumeEvent(timestamp="t", category="RESUME", previous_workflow_id="w1",
                    new_workflow_id="w2", checkpoint_hash="abc", completed_agents=["a"])
    assert e.completed_agents == ["a"]


def test_all_events_are_frozen():
    for ctor in [
        lambda: WorkflowHeader(timestamp="t", category="HEADER", workflow_id=None, target_url=None),
        lambda: PhaseEvent(timestamp="t", category="PHASE", phase="p", event="start"),
        lambda: AgentEvent(timestamp="t", category="AGENT", agent_name="a", event="start", attempt=1),
        lambda: ToolCallEvent(timestamp="t", category="TOOL", agent_name="a", tool_name="T", parameters={}),
        lambda: LlmTurnEvent(timestamp="t", category="LLM", agent_name="a", turn=1, content="c"),
        lambda: ErrorEvent(timestamp="t", category="ERROR", error_type="E", message="m"),
        lambda: SummaryEvent(timestamp="t", category="SUMMARY", status="completed",
                             total_duration_ms=1, total_cost_usd=0.0, agents=[]),
        lambda: ResumeEvent(timestamp="t", category="RESUME", previous_workflow_id="a",
                            new_workflow_id="b", checkpoint_hash="h", completed_agents=[]),
    ]:
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctor().timestamp = "mutated"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.display.events`.

- [ ] **Step 3: Write implementation**

Create `packages/core/src/shannon_core/display/events.py`:

```python
"""DisplayEvent family — immutable pure-data representations of log activity.

The single source of truth: both renderers consume these events. Events carry
NO rendering logic, so they can be replayed, serialized, and tested in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class DisplayEvent:
    """Base for all display events."""

    timestamp: str
    category: str


@dataclass(frozen=True)
class WorkflowHeader(DisplayEvent):
    workflow_id: str | None
    target_url: str | None


@dataclass(frozen=True)
class PhaseEvent(DisplayEvent):
    phase: str
    event: Literal["start", "complete"]


@dataclass(frozen=True)
class AgentEvent(DisplayEvent):
    agent_name: str
    event: Literal["start", "end"]
    attempt: int
    duration_ms: int | None = None
    cost_usd: float | None = None
    success: bool | None = None
    error: str | None = None


@dataclass(frozen=True)
class ToolCallEvent(DisplayEvent):
    agent_name: str
    tool_name: str
    parameters: Any


@dataclass(frozen=True)
class LlmTurnEvent(DisplayEvent):
    agent_name: str
    turn: int
    content: str


@dataclass(frozen=True)
class ErrorEvent(DisplayEvent):
    error_type: str
    message: str
    context: str | None = None
    classified: str | None = None
    display_retryable: bool | None = None


@dataclass(frozen=True)
class AgentMetric:
    name: str
    duration_ms: int
    cost_usd: float | None = None
    success: bool = True


@dataclass(frozen=True)
class SummaryEvent(DisplayEvent):
    status: str
    total_duration_ms: int
    total_cost_usd: float
    agents: list[AgentMetric] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class ResumeEvent(DisplayEvent):
    previous_workflow_id: str
    new_workflow_id: str
    checkpoint_hash: str
    completed_agents: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_events.py packages/core/tests/display/test_types.py -v`
Expected: PASS (both events and types tests).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/events.py packages/core/tests/display/test_events.py
git commit -m "feat(display): add immutable DisplayEvent dataclass family"
```

---

### Task 4: DisplayDispatcher

**Files:**
- Create: `packages/core/src/shannon_core/display/dispatcher.py`
- Test: `packages/core/tests/display/test_dispatcher.py`

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/display/test_dispatcher.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_dispatcher.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.display.dispatcher`.

- [ ] **Step 3: Write implementation**

Create `packages/core/src/shannon_core/display/dispatcher.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_dispatcher.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/dispatcher.py packages/core/tests/display/test_dispatcher.py
git commit -m "feat(display): add DisplayDispatcher event fan-out"
```

---

## Phase 2 — Formatters + FileLogRenderer

### Task 5: Migrate format_* helpers to core, re-export from whitebox

**Files:**
- Create: `packages/core/src/shannon_core/display/formatters.py` (format_* portion only)
- Modify: `packages/whitebox/src/shannon_whitebox/audit/utils.py:1-31`

- [ ] **Step 1: Write the failing test in core**

Create `packages/core/tests/display/test_formatters.py`:

```python
from shannon_core.display.formatters import format_duration, format_log_time, format_timestamp


def test_format_duration_milliseconds():
    assert format_duration(23) == "23ms"


def test_format_duration_seconds():
    assert format_duration(1500) == "1.5s"


def test_format_duration_minutes():
    assert format_duration(150000) == "2m 30s"


def test_format_timestamp_is_iso8601_with_z():
    ts = format_timestamp(1700000000123 / 1000)
    assert ts.endswith("Z")
    assert "T" in ts


def test_format_log_time_format():
    # format_log_time uses local now; just assert shape YYYY-MM-DD HH:MM:SS
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", format_log_time())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_formatters.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.display.formatters`.

- [ ] **Step 3: Create formatters.py with migrated helpers**

Create `packages/core/src/shannon_core/display/formatters.py`:

```python
"""Display formatters: migrated time helpers + display-enhancement functions.

format_duration/format_log_time/format_timestamp are migrated here from
shannon_whitebox.audit.utils so core renderers can use them without a reverse
dependency on whitebox. Whitebox re-imports them for backward compatibility.
"""
from __future__ import annotations

from datetime import datetime, timezone


def format_duration(ms: int) -> str:
    """Convert milliseconds to human-readable: '23ms', '1.5s', '2m 30s'."""
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remaining = int(seconds % 60)
    return f"{minutes}m {remaining}s"


def format_timestamp(ts: float | None = None) -> str:
    """ISO 8601 UTC string with milliseconds. Defaults to now."""
    if ts is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def format_log_time() -> str:
    """Human-readable local format 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
```

- [ ] **Step 4: Run core test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_formatters.py -v`
Expected: PASS.

- [ ] **Step 5: Update whitebox utils to re-import**

In `packages/whitebox/src/shannon_whitebox/audit/utils.py`, replace the three function definitions (`format_duration`, `format_timestamp`, `format_log_time` — currently lines 7-30) with re-imports. The file's new top section becomes:

```python
from pathlib import Path

from shannon_core.display.formatters import (
    format_duration,
    format_log_time,
    format_timestamp,
)
from shannon_core.models.metrics import SessionMetadata


def sanitize_hostname(url: str) -> str:
    """Extract and sanitize hostname from URL for use as a directory-safe identifier."""
    hostname = url.replace("https://", "").replace("http://", "").split("/")[0]
    return hostname.replace(".", "-").replace(":", "-")


# ... keep generate_audit_path, generate_log_path, generate_prompt_path,
# generate_workflow_log_path, generate_session_json_path,
# initialize_audit_structure unchanged (they were lines 33+).
```

(Remove the now-duplicate `format_*` function bodies; keep everything from `sanitize_hostname` onward.)

- [ ] **Step 6: Verify whitebox tests still pass (no regression)**

Run: `uv run pytest packages/whitebox/tests/ -v`
Expected: PASS — all existing whitebox tests still pass (they import `format_*` from `shannon_whitebox.audit.utils`, which now re-exports from core).

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/display/formatters.py packages/core/tests/display/test_formatters.py packages/whitebox/src/shannon_whitebox/audit/utils.py
git commit -m "refactor(display): migrate format_* to core, whitebox re-imports"
```

---

### Task 6: agent_prefix formatter

**Files:**
- Modify: `packages/core/src/shannon_core/display/formatters.py`
- Modify: `packages/core/tests/display/test_formatters.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/core/tests/display/test_formatters.py`:

```python
from shannon_core.display.formatters import agent_prefix


def test_agent_prefix_known_vuln_agents():
    assert agent_prefix("injection-vuln") == "[Injection]"
    assert agent_prefix("xss-vuln") == "[XSS]"
    assert agent_prefix("ssrf-vuln") == "[SSRF]"
    assert agent_prefix("auth-vuln") == "[Auth]"
    assert agent_prefix("authz-vuln") == "[Authz]"


def test_agent_prefix_exploit_variants_share_prefix():
    assert agent_prefix("injection-exploit") == "[Injection]"
    assert agent_prefix("authz-exploit") == "[Authz]"
    assert agent_prefix("auth-exploit") == "[Auth]"


def test_agent_prefix_unknown_falls_back():
    assert agent_prefix("pre-recon") == "[Agent]"
    assert agent_prefix("totally-unknown") == "[Agent]"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_formatters.py -k agent_prefix -v`
Expected: FAIL — `ImportError: cannot import name 'agent_prefix'`.

- [ ] **Step 3: Implement agent_prefix**

Append to `packages/core/src/shannon_core/display/formatters.py`:

```python
# Maps agent name (AgentName.value string) to a short display prefix.
# Keys are matched exactly, so auth/authz cannot collide. The original TS
# project used substring matching where authz HAD to be checked before auth;
# exact-key matching removes that hazard.
_AGENT_PREFIXES: dict[str, str] = {
    "injection-vuln": "[Injection]",
    "injection-exploit": "[Injection]",
    "xss-vuln": "[XSS]",
    "xss-exploit": "[XSS]",
    "authz-vuln": "[Authz]",
    "authz-exploit": "[Authz]",
    "auth-vuln": "[Auth]",
    "auth-exploit": "[Auth]",
    "ssrf-vuln": "[SSRF]",
    "ssrf-exploit": "[SSRF]",
}


def agent_prefix(agent_name: str) -> str:
    """Map an agent name to its display prefix, or '[Agent]' if unknown."""
    return _AGENT_PREFIXES.get(agent_name, "[Agent]")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_formatters.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/formatters.py packages/core/tests/display/test_formatters.py
git commit -m "feat(display): add agent_prefix mapping"
```

---

### Task 7: summarize_todo + format_error_block formatters

**Files:**
- Modify: `packages/core/src/shannon_core/display/formatters.py`
- Modify: `packages/core/tests/display/test_formatters.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/display/test_formatters.py`:

```python
from shannon_core.display.formatters import format_error_block, summarize_todo


def test_summarize_todo_shows_latest_completed():
    params = {"todos": [
        {"status": "completed", "content": "step one"},
        {"status": "completed", "content": "step two"},
        {"status": "in_progress", "content": "step three"},
    ]}
    assert summarize_todo(params) == "✅ step two"


def test_summarize_todo_shows_in_progress_when_none_completed():
    params = {"todos": [
        {"status": "in_progress", "content": "current"},
    ]}
    assert summarize_todo(params) == "🔄 current"


def test_summarize_todo_returns_none_when_empty():
    assert summarize_todo({"todos": []}) is None
    assert summarize_todo({}) is None


def test_format_error_block_pipe_delimited():
    result = format_error_block("phase context|ErrorType|message|Hint: retry")
    lines = result.split("\n")
    assert lines[0] == "Error:       phase context"
    assert lines[1] == "             ErrorType"
    assert lines[2] == "             message"
    assert lines[3] == "             Hint: retry"


def test_format_error_block_single_segment():
    assert format_error_block("just one error") == "Error:       just one error\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_formatters.py -k "summarize_todo or format_error_block" -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement**

Append to `packages/core/src/shannon_core/display/formatters.py`:

```python
def summarize_todo(params: dict) -> str | None:
    """Summarize a TodoWrite tool call: latest completed (✅) or first in-progress (🔄).

    Returns None if nothing noteworthy, so callers can skip emitting the line.
    """
    todos = params.get("todos")
    if not todos or not isinstance(todos, list):
        return None
    completed = [t for t in todos if t.get("status") == "completed"]
    if completed:
        return f"✅ {completed[-1].get('content', '')}"
    in_progress = [t for t in todos if t.get("status") == "in_progress"]
    if in_progress:
        return f"🔄 {in_progress[0].get('content', '')}"
    return None


def format_error_block(error_str: str) -> str:
    """Format a pipe-delimited error string into aligned multi-line text.

    Input:  "phase context|ErrorType|message|Hint: ..."
    Output: "Error:       phase context\\n             ErrorType\\n             ..."
    """
    label = "Error:       "
    indent = " " * len(label)
    segments = error_str.split("|")
    rendered = [
        f"{label}{seg.strip()}" if i == 0 else f"{indent}{seg.strip()}"
        for i, seg in enumerate(segments)
    ]
    return "\n".join(rendered) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_formatters.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/formatters.py packages/core/tests/display/test_formatters.py
git commit -m "feat(display): add summarize_todo and format_error_block"
```

---

### Task 8: humanize_tool_call + maybe_browser_action formatters

**Files:**
- Modify: `packages/core/src/shannon_core/display/formatters.py`
- Modify: `packages/core/tests/display/test_formatters.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/display/test_formatters.py`:

```python
from shannon_core.display.formatters import humanize_tool_call, maybe_browser_action


def test_humanize_task_launch():
    result = humanize_tool_call("Task", {"description": "deep analysis"})
    assert result == "🚀 Launching deep analysis"


def test_humanize_todowrite_uses_summarize():
    result = humanize_tool_call("TodoWrite", {"todos": [
        {"status": "completed", "content": "done thing"},
    ]})
    assert result == "✅ done thing"


def test_humanize_todowrite_none_returns_placeholder():
    # summarize_todo can return None; humanize falls back to a generic line
    result = humanize_tool_call("TodoWrite", {"todos": []})
    assert result == "TodoWrite"


def test_humanize_bash_browser_action():
    result = humanize_tool_call("Bash", {"command": "playwright-cli navigate https://x.com"})
    assert "🌐" in result
    assert "x.com" in result


def test_humanize_bash_non_browser():
    result = humanize_tool_call("Bash", {"command": "ls -la"})
    assert "command=ls -la" in result


def test_humanize_unknown_tool_default_params():
    result = humanize_tool_call("Read", {"file_path": "/tmp/x"})
    assert "file_path=/tmp/x" in result


def test_maybe_browser_action_navigate():
    assert maybe_browser_action({"command": "playwright-cli goto https://a.com"}) == "🌐 Navigating to a.com"


def test_maybe_browser_action_click():
    assert maybe_browser_action({"command": "playwright-cli click #submit"}) == "🖱️ Clicking #submit"


def test_maybe_browser_action_non_browser_returns_none():
    assert maybe_browser_action({"command": "ls -la"}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_formatters.py -k "humanize or maybe_browser" -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement**

Append to `packages/core/src/shannon_core/display/formatters.py`:

```python
import re

from urllib.parse import urlparse


def default_tool_params(tool_name: str, params: dict) -> str:
    """Generic per-tool smart truncation for readable log lines."""
    tool_key_map = {
        "Bash": "command",
        "Read": "file_path",
        "Write": "file_path",
        "Edit": "file_path",
        "Grep": "pattern",
        "Glob": "pattern",
    }
    key = tool_key_map.get(tool_name)
    if key and key in params:
        val = str(params[key])
        if len(val) > 80:
            val = val[:77] + "..."
        return f"{key}={val}"
    items = list(params.items())[:2]
    parts = [f"{k}={str(v)[:40]}" for k, v in items]
    result = ", ".join(parts)
    if len(params) > 2:
        result += ", ..."
    return result


def maybe_browser_action(params: dict) -> str | None:
    """Parse a playwright-cli Bash command into an emoji phrase. None if not browser."""
    command = params.get("command", "") if isinstance(params, dict) else ""
    match = re.match(r"playwright-cli\s+(?:-s=\S+\s+)?(\S+)(?:\s+(.*))?", command)
    if not match:
        return None
    subcommand, args = match.group(1), (match.group(2) or "").strip()

    def _domain(url: str) -> str:
        try:
            host = urlparse(url).hostname
            return host or url[:30]
        except Exception:
            return url[:30]

    if subcommand in ("open", "goto"):
        return f"🌐 Navigating to {_domain(args)}" if args else "🌐 Opening browser"
    if subcommand in ("click", "dblclick"):
        return f"🖱️ Clicking {(args or 'element')[:25]}"
    if subcommand in ("type", "fill"):
        return f"⌨️ Typing {(args or 'text')[:20]}"
    if subcommand in ("snapshot", "screenshot"):
        return "📸 Taking page snapshot" if subcommand == "snapshot" else "📸 Taking screenshot"
    if subcommand == "reload":
        return "🔄 Reloading page"
    return f"🌐 Browser: {subcommand}"


def humanize_tool_call(tool_name: str, params: dict) -> str:
    """Turn a raw tool call into a human-readable single line."""
    if not isinstance(params, dict):
        params = {}
    match tool_name:
        case "Task":
            return f"🚀 Launching {params.get('description', 'analysis agent')}"
        case "TodoWrite":
            return summarize_todo(params) or "TodoWrite"
        case "Bash":
            browser = maybe_browser_action(params)
            if browser:
                return browser
            return default_tool_params(tool_name, params)
        case _:
            return default_tool_params(tool_name, params)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_formatters.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/formatters.py packages/core/tests/display/test_formatters.py
git commit -m "feat(display): add humanize_tool_call and maybe_browser_action"
```

---

### Task 9: FileLogRenderer — header + phase

**Files:**
- Create: `packages/core/src/shannon_core/display/file_renderer.py`
- Test: `packages/core/tests/display/test_file_renderer.py`

The renderer takes a `LineWriter` (injected; in production this is whitebox's `LogStream`). For testing we use a `FakeWriter` that collects strings.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/display/test_file_renderer.py`:

```python
from shannon_core.display.events import PhaseEvent, WorkflowHeader
from shannon_core.display.file_renderer import FileLogRenderer


class FakeWriter:
    def __init__(self):
        self.chunks: list[str] = []

    async def write(self, text: str) -> None:
        self.chunks.append(text)

    @property
    def text(self) -> str:
        return "".join(self.chunks)


async def test_header_includes_workflow_id_and_target():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(WorkflowHeader(
        timestamp="2026-01-01 12:00:00", category="HEADER",
        workflow_id="wf-1", target_url="https://x.com"))
    out = renderer._writer.text
    assert "Shannon Pentest - Workflow Log" in out
    assert "Workflow ID: wf-1" in out
    assert "Target URL:  https://x.com" in out
    assert "Started:     2026-01-01 12:00:00" in out
    assert out.count("=" * 80) == 3


async def test_phase_start_prepends_blank_line():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(PhaseEvent(
        timestamp="2026-01-01 12:00:00", category="PHASE",
        phase="reconnaissance", event="start"))
    out = renderer._writer.text
    assert out.startswith("\n")
    assert "[PHASE] Starting reconnaissance" in out


async def test_phase_complete_no_blank_prefix():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(PhaseEvent(
        timestamp="t", category="PHASE", phase="recon", event="complete"))
    out = renderer._writer.text
    assert "[PHASE] Completed recon" in out
    assert not out.startswith("\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_file_renderer.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.display.file_renderer`.

- [ ] **Step 3: Implement**

Create `packages/core/src/shannon_core/display/file_renderer.py`:

```python
"""FileLogRenderer — renders DisplayEvents to plain text for workflow.log.

No ANSI codes (must stay grep-able and tail-friendly). Backward-compatible:
the summary block always contains a 'Workflow COMPLETED' or 'Workflow FAILED'
line, matching the COMPLETION_PATTERN in shannon_core.cli.logs.
"""
from __future__ import annotations

_SEP = "=" * 80


class FileLogRenderer:
    def __init__(self, writer) -> None:
        # writer satisfies shannon_core.display.types.LineWriter (async write(str))
        self._writer = writer

    async def render(self, event) -> None:
        from shannon_core.display.events import (
            AgentEvent, ErrorEvent, LlmTurnEvent, PhaseEvent,
            ResumeEvent, SummaryEvent, ToolCallEvent, WorkflowHeader,
        )
        match event:
            case WorkflowHeader(): await self._writer.write(self._header(event))
            case PhaseEvent(): await self._writer.write(self._phase(event))
            case AgentEvent(): await self._writer.write(self._agent(event))
            case ToolCallEvent(): await self._writer.write(self._tool(event))
            case LlmTurnEvent(): await self._writer.write(self._llm(event))
            case ErrorEvent(): await self._writer.write(self._error(event))
            case SummaryEvent(): await self._writer.write(self._summary(event))
            case ResumeEvent(): await self._writer.write(self._resume(event))

    def _header(self, e) -> str:
        target = e.target_url if e.target_url else "N/A"
        lines = [_SEP, "Shannon Pentest - Workflow Log", _SEP]
        if e.workflow_id:  # omit the line entirely when None (matches old behavior)
            lines.append(f"Workflow ID: {e.workflow_id}")
        lines.append(f"Target URL:  {target}")
        lines.append(f"Started:     {e.timestamp}")
        lines.append(_SEP)
        return "\n".join(lines) + "\n\n"

    def _phase(self, e) -> str:
        verb = "Starting" if e.event == "start" else "Completed"
        prefix = "\n" if e.event == "start" else ""
        return f"{prefix}[{e.timestamp}] [PHASE] {verb} {e.phase}\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_file_renderer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/file_renderer.py packages/core/tests/display/test_file_renderer.py
git commit -m "feat(display): FileLogRenderer header and phase rendering"
```

---

### Task 10: FileLogRenderer — agent + tool + llm

**Files:**
- Modify: `packages/core/src/shannon_core/display/file_renderer.py`
- Modify: `packages/core/tests/display/test_file_renderer.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/display/test_file_renderer.py`:

```python
from shannon_core.display.events import AgentEvent, LlmTurnEvent, ToolCallEvent


async def test_agent_start_with_prefix():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="injection-vuln",
        event="start", attempt=2))
    assert "[AGENT] [Injection] injection-vuln: Starting (attempt 2)\n" in renderer._writer.text


async def test_agent_start_no_prefix_for_unknown():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="pre-recon",
        event="start", attempt=1))
    assert "[AGENT] pre-recon: Starting (attempt 1)\n" in renderer._writer.text


async def test_agent_end_completed_with_metrics():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="xss-vuln",
        event="end", attempt=1, duration_ms=5200, cost_usd=0.15, success=True))
    line = "[AGENT] [XSS] xss-vuln: Completed (5.2s, $0.1500)\n"
    assert line in renderer._writer.text


async def test_agent_end_failed():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="xss-vuln",
        event="end", attempt=1, duration_ms=100, success=False, error="boom"))
    assert "[AGENT] [XSS] xss-vuln: Failed (100ms) - boom" in renderer._writer.text


async def test_tool_line_alignment():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(ToolCallEvent(
        timestamp="t", category="TOOL", agent_name="injection-vuln",
        tool_name="Bash", parameters={"command": "ls"}))
    out = renderer._writer.text
    assert "[TOOL]  [Injection] injection-vuln: Bash: command=ls\n" in out  # two spaces after [TOOL]


async def test_llm_line_alignment():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(LlmTurnEvent(
        timestamp="t", category="LLM", agent_name="injection-vuln",
        turn=1, content="Analyzing"))
    out = renderer._writer.text
    assert "[LLM]   [Injection] injection-vuln: Turn 1: Analyzing\n" in out  # three spaces after [LLM]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_file_renderer.py -k "agent or tool or llm" -v`
Expected: FAIL — agent/tool/llm methods return nothing (AttributeError or no output).

- [ ] **Step 3: Implement**

Append to `packages/core/src/shannon_core/display/file_renderer.py`:

```python
from shannon_core.display.formatters import agent_prefix, format_duration, humanize_tool_call


def _prefixed(agent_name: str) -> str:
    """Return '[Prefix] agentname' or just 'agentname' for unknown agents."""
    pfx = agent_prefix(agent_name)
    if pfx == "[Agent]":
        return agent_name
    return f"{pfx} {agent_name}"
```

Then add these methods to the `FileLogRenderer` class:

```python
    def _agent(self, e) -> str:
        who = _prefixed(e.agent_name)
        if e.event == "start":
            return f"[{e.timestamp}] [AGENT] {who}: Starting (attempt {e.attempt})\n"
        # end
        if e.success is False:
            dur = format_duration(e.duration_ms) if e.duration_ms is not None else "?"
            err = f" - {e.error}" if e.error else ""
            return f"[{e.timestamp}] [AGENT] {who}: Failed ({dur}){err}\n"
        parts = []
        if e.duration_ms is not None:
            parts.append(format_duration(e.duration_ms))
        if e.cost_usd is not None:
            parts.append(f"${e.cost_usd:.4f}")
        metrics = f" ({', '.join(parts)})" if parts else ""
        return f"[{e.timestamp}] [AGENT] {who}: Completed{metrics}\n"

    def _tool(self, e) -> str:
        who = _prefixed(e.agent_name)
        params = humanize_tool_call(e.tool_name, e.parameters if isinstance(e.parameters, dict) else {})
        return f"[{e.timestamp}] [TOOL]  {who}: {e.tool_name}: {params}\n"

    def _llm(self, e) -> str:
        who = _prefixed(e.agent_name)
        content = e.content[:200] + "..." if len(e.content) > 200 else e.content
        return f"[{e.timestamp}] [LLM]   {who}: Turn {e.turn}: {content}\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_file_renderer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/file_renderer.py packages/core/tests/display/test_file_renderer.py
git commit -m "feat(display): FileLogRenderer agent/tool/llm rendering with prefixes"
```

---

### Task 11: FileLogRenderer — error + summary + resume

**Files:**
- Modify: `packages/core/src/shannon_core/display/file_renderer.py`
- Modify: `packages/core/tests/display/test_file_renderer.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/display/test_file_renderer.py`:

```python
from shannon_core.display.events import AgentMetric, ErrorEvent, ResumeEvent, SummaryEvent


async def test_error_line_basic():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(ErrorEvent(
        timestamp="t", category="ERROR", error_type="ValueError", message="boom"))
    assert "[ERROR] ValueError: boom\n" in renderer._writer.text


async def test_error_line_with_context_and_classification():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(ErrorEvent(
        timestamp="t", category="ERROR", error_type="RuntimeError", message="x",
        context="during scan", classified="BillingError", display_retryable=True))
    line = renderer._writer.text
    assert "[ERROR] RuntimeError: x (context: during scan) [BillingError · retryable]" in line


async def test_summary_completed_has_completion_marker():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(SummaryEvent(
        timestamp="t", category="SUMMARY", status="completed",
        total_duration_ms=12400, total_cost_usd=0.3450,
        agents=[AgentMetric(name="xss-vuln", duration_ms=4100, cost_usd=0.165, success=True)]))
    out = renderer._writer.text
    assert "Workflow COMPLETED" in out  # COMPLETION_PATTERN must match
    assert "Status:      completed" in out
    assert "Duration:    12.4s" in out
    assert "Total Cost:  $0.3450" in out
    assert "✓ xss-vuln" in out


async def test_summary_failed_has_failure_marker():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(SummaryEvent(
        timestamp="t", category="SUMMARY", status="failed",
        total_duration_ms=1000, total_cost_usd=0.0, agents=[], error="something|went|wrong"))
    out = renderer._writer.text
    assert "Workflow FAILED" in out
    assert "Error:       something" in out


async def test_resume_block():
    renderer = FileLogRenderer(FakeWriter())
    await renderer.render(ResumeEvent(
        timestamp="t", category="RESUME", previous_workflow_id="w1",
        new_workflow_id="w2", checkpoint_hash="abc", completed_agents=["a", "b"]))
    out = renderer._writer.text
    assert "[RESUME] Resuming workflow" in out
    assert "Previous Workflow ID: w1" in out
    assert "New Workflow ID:      w2" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_file_renderer.py -k "error or summary or resume" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Append these methods to the `FileLogRenderer` class in `packages/core/src/shannon_core/display/file_renderer.py`:

```python
    def _error(self, e) -> str:
        msg = f"[{e.timestamp}] [ERROR] {e.error_type}: {e.message}"
        if e.context:
            msg += f" (context: {e.context})"
        if e.classified:
            flag = "retryable" if e.display_retryable else "non-retryable"
            msg += f" [{e.classified} · {flag}]"
        return msg + "\n"

    def _summary(self, e) -> str:
        status = "COMPLETED" if e.status == "completed" else "FAILED"
        lines = [
            "",
            f"{_SEP}",
            f"Workflow {status}",
            "─" * 40,
            f"Status:      {e.status}",
            f"Duration:    {format_duration(e.total_duration_ms)}",
            f"Total Cost:  ${e.total_cost_usd:.4f}",
            f"Agents:      {len(e.agents)} completed",
            "",
            "Agent Breakdown:",
        ]
        for m in e.agents:
            mark = "✓" if m.success else "✗"
            cost = f", ${m.cost_usd:.4f}" if m.cost_usd is not None else ""
            lines.append(f"  {mark} {m.name} ({format_duration(m.duration_ms)}{cost})")
        if e.error:
            lines.append("")
            lines.append(format_error_block(e.error).rstrip("\n"))
        lines.append(f"{_SEP}")
        lines.append("")
        return "\n".join(lines)

    def _resume(self, e) -> str:
        return (
            f"\n[{e.timestamp}] [RESUME] Resuming workflow\n"
            f"  Previous Workflow ID: {e.previous_workflow_id}\n"
            f"  New Workflow ID:      {e.new_workflow_id}\n"
            f"  Checkpoint:           {e.checkpoint_hash}\n"
            f"  Completed Agents:     {', '.join(e.completed_agents)}\n\n"
        )
```

Also add `format_error_block` to the existing import line at the top of `file_renderer.py`:

```python
from shannon_core.display.formatters import (
    agent_prefix, format_duration, format_error_block, humanize_tool_call,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_file_renderer.py -v`
Expected: PASS.

- [ ] **Step 5: Run backward-compat check (COMPLETION_PATTERN still matches)**

Run: `uv run python -c "import re; from shannon_core.cli.logs import COMPLETION_PATTERN; print(bool(COMPLETION_PATTERN.search('Workflow COMPLETED\n'))); print(bool(COMPLETION_PATTERN.search('Workflow FAILED\n')))"`

Expected: prints `True` then `True`.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/display/file_renderer.py packages/core/tests/display/test_file_renderer.py
git commit -m "feat(display): FileLogRenderer error/summary/resume with completion marker"
```

---

## Phase 3 — Error Classification

### Task 12: ErrorType enum + classify_for_temporal

**Files:**
- Create: `packages/core/src/shannon_core/errors/__init__.py`
- Create: `packages/core/src/shannon_core/errors/classification.py`
- Test: `packages/core/tests/errors/test_classification.py`

- [ ] **Step 1: Create package marker**

Create `packages/core/src/shannon_core/errors/__init__.py`:

```python
"""Error classification for display and Temporal retry decisions."""
```

- [ ] **Step 2: Write the failing test**

Create `packages/core/tests/errors/test_classification.py`:

```python
import pytest

from shannon_core.errors.classification import ErrorType, classify_for_temporal


@pytest.mark.parametrize("message,expected_type,expected_retryable", [
    ("billing API charge failed", ErrorType.BILLING, True),
    ("rate limit exceeded, retry later", ErrorType.RATE_LIMIT, True),
    ("401 unauthorized", ErrorType.AUTHENTICATION, False),
    ("403 forbidden", ErrorType.PERMISSION, False),
    ("output validation failed", ErrorType.OUTPUT_VALIDATION, True),
    ("400 invalid request body", ErrorType.INVALID_REQUEST, False),
    ("ENOENT config not found", ErrorType.CONFIG, False),
    ("max turns limit reached", ErrorType.EXECUTION_LIMIT, False),
    ("budget exceeded", ErrorType.EXECUTION_LIMIT, False),
    ("some random transient glitch", ErrorType.TRANSIENT, True),
])
def test_classify_for_temporal_match_order(message, expected_type, expected_retryable):
    err = RuntimeError(message)
    etype, retryable = classify_for_temporal(err)
    assert etype == expected_type
    assert retryable == expected_retryable


def test_classify_for_temporal_fallback_is_retryable():
    # Unknown errors fall through to TRANSIENT (retryable) for Temporal backoff
    etype, retryable = classify_for_temporal(RuntimeError("totally unknown xyz"))
    assert etype == ErrorType.TRANSIENT
    assert retryable is True


def test_authz_validation_order_validation_before_invalid_request():
    # OUTPUT_VALIDATION must match before INVALID_REQUEST even if both keywords present
    etype, _ = classify_for_temporal(RuntimeError("output validation failed with 400"))
    assert etype == ErrorType.OUTPUT_VALIDATION
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/errors/test_classification.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.errors.classification`.

- [ ] **Step 4: Implement**

Create `packages/core/src/shannon_core/errors/classification.py`:

```python
"""Error classification — TWO functions with OPPOSITE fallback semantics.

classify_for_temporal:        fallback retryable=True (delegate to Temporal backoff)
is_retryable_for_display:     fallback retryable=False (fail-safe display)

Match order mirrors the original Shannon (TS) classifyErrorForTemporal exactly:
Billing -> Authentication -> Permission -> OutputValidation (before InvalidRequest)
-> InvalidRequest -> RequestTooLarge -> Configuration -> ExecutionLimit -> InvalidTarget
-> TRANSIENT fallback.
"""
from __future__ import annotations

from enum import StrEnum


class ErrorType(StrEnum):
    BILLING = "BillingError"
    RATE_LIMIT = "RateLimitError"
    AUTHENTICATION = "AuthenticationError"
    PERMISSION = "PermissionError"
    OUTPUT_VALIDATION = "OutputValidationError"
    INVALID_REQUEST = "InvalidRequestError"
    CONFIG = "ConfigurationError"
    EXECUTION_LIMIT = "ExecutionLimitError"
    INVALID_TARGET = "InvalidTargetError"
    TRANSIENT = "TransientError"


# (substring-pattern, type, retryable) — ORDER MATTERS. First match wins.
_TEMPORAL_PATTERNS: list[tuple[str, ErrorType, bool]] = [
    ("billing", ErrorType.BILLING, True),
    ("charge failed", ErrorType.BILLING, True),
    ("rate limit", ErrorType.RATE_LIMIT, True),
    ("429", ErrorType.RATE_LIMIT, True),
    ("401", ErrorType.AUTHENTICATION, False),
    ("unauthorized", ErrorType.AUTHENTICATION, False),
    ("403", ErrorType.PERMISSION, False),
    ("forbidden", ErrorType.PERMISSION, False),
    ("output validation", ErrorType.OUTPUT_VALIDATION, True),   # before 400/invalid request
    ("400", ErrorType.INVALID_REQUEST, False),
    ("invalid request", ErrorType.INVALID_REQUEST, False),
    ("413", ErrorType.INVALID_REQUEST, False),
    ("request too large", ErrorType.INVALID_REQUEST, False),
    ("ENOENT", ErrorType.CONFIG, False),
    ("config", ErrorType.CONFIG, False),
    ("max turns", ErrorType.EXECUTION_LIMIT, False),
    ("budget", ErrorType.EXECUTION_LIMIT, False),
    ("limit reached", ErrorType.EXECUTION_LIMIT, False),
]


def classify_for_temporal(error: Exception) -> tuple[ErrorType, bool]:
    """Classify an error for Temporal retry policy.

    Fallback: TRANSIENT + retryable=True (let Temporal backoff handle it).
    """
    msg = str(error).lower()
    for pattern, etype, retryable in _TEMPORAL_PATTERNS:
        if pattern.lower() in msg:
            return etype, retryable
    return ErrorType.TRANSIENT, True
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/errors/test_classification.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/errors/__init__.py packages/core/src/shannon_core/errors/classification.py packages/core/tests/errors/test_classification.py
git commit -m "feat(errors): add ErrorType and classify_for_temporal with strict match order"
```

---

### Task 13: is_retryable_for_display (opposite fallback)

**Files:**
- Modify: `packages/core/src/shannon_core/errors/classification.py`
- Modify: `packages/core/tests/errors/test_classification.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/errors/test_classification.py`:

```python
from shannon_core.errors.classification import is_retryable_for_display


def test_display_retryable_true_for_known_retryable():
    assert is_retryable_for_display(RuntimeError("rate limit exceeded")) is True
    assert is_retryable_for_display(RuntimeError("billing charge failed")) is True


def test_display_retryable_false_for_known_non_retryable():
    assert is_retryable_for_display(RuntimeError("401 unauthorized")) is False
    assert is_retryable_for_display(RuntimeError("ENOENT config not found")) is False


def test_display_retryable_fallback_is_false():
    # OPPOSITE of classify_for_temporal: unknown errors are NOT retryable (fail-safe)
    assert is_retryable_for_display(RuntimeError("totally unknown xyz")) is False


def test_two_functions_agree_on_known_types_but_differ_on_unknown():
    from shannon_core.errors.classification import classify_for_temporal
    known = RuntimeError("rate limit exceeded")
    # Agree on known retryable
    assert classify_for_temporal(known)[1] is True
    assert is_retryable_for_display(known) is True
    # Differ on unknown
    unknown = RuntimeError("totally unknown glitch")
    assert classify_for_temporal(unknown)[1] is True   # Temporal: retry
    assert is_retryable_for_display(unknown) is False  # Display: fail-safe
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/errors/test_classification.py -k "display_retryable or two_functions" -v`
Expected: FAIL — `ImportError: is_retryable_for_display`.

- [ ] **Step 3: Implement**

Append to `packages/core/src/shannon_core/errors/classification.py`:

```python
# Display-only patterns. NOTE: the set of "retryable" and "non-retryable"
# keywords is curated independently; unknown errors default to NON-retryable
# (fail-safe), which is the OPPOSITE of classify_for_temporal's fallback.
_NON_RETRYABLE_KEYWORDS = (
    "401", "unauthorized", "403", "forbidden", "400", "invalid request",
    "413", "request too large", "ENOENT", "config", "max turns", "budget",
    "invalid prompt", "out of memory", "permission denied", "invalid api key",
)
_RETRYABLE_KEYWORDS = (
    "rate limit", "429", "timeout", "network", "ECONN", "billing",
    "transient", "502", "503", "504",
)


def is_retryable_for_display(error: Exception) -> bool:
    """Display-only retry flag. Fallback: False (fail-safe).

    Semantics intentionally DIFFER from classify_for_temporal, which falls
    back to retryable=True. Do not merge these two functions.
    """
    msg = str(error).lower()
    for kw in _NON_RETRYABLE_KEYWORDS:
        if kw.lower() in msg:
            return False
    for kw in _RETRYABLE_KEYWORDS:
        if kw.lower() in msg:
            return True
    return False  # fail-safe default for unknown errors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/errors/test_classification.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/errors/classification.py packages/core/tests/errors/test_classification.py
git commit -m "feat(errors): add is_retryable_for_display with fail-safe (opposite) fallback"
```

---

## Phase 4 — Rich Renderer + Integration

### Task 14: RichConsoleRenderer — STYLE_MAP + header + phase

**Files:**
- Create: `packages/core/src/shannon_core/display/rich_renderer.py`
- Test: `packages/core/tests/display/test_rich_renderer.py`

Tests capture rendered output via a `Console(file=StringIO())` so assertions are deterministic.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/display/test_rich_renderer.py`:

```python
import io

from rich.console import Console

from shannon_core.display.events import PhaseEvent, WorkflowHeader
from shannon_core.display.rich_renderer import RichConsoleRenderer


def _renderer_with_capture() -> tuple[RichConsoleRenderer, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, record=True)
    return RichConsoleRenderer(console), buf


async def test_header_renders_workflow_id_and_target():
    renderer, buf = _renderer_with_capture()
    await renderer.render(WorkflowHeader(
        timestamp="2026-01-01 12:00:00", category="HEADER",
        workflow_id="wf-1", target_url="https://x.com"))
    out = renderer._console.export_text()
    assert "Shannon Pentest" in out
    assert "wf-1" in out
    assert "https://x.com" in out


async def test_phase_start_renders_phase_name():
    renderer, _ = _renderer_with_capture()
    await renderer.render(PhaseEvent(
        timestamp="t", category="PHASE", phase="reconnaissance", event="start"))
    out = renderer._console.export_text()
    assert "reconnaissance" in out
    assert "Starting" in out or "started" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.display.rich_renderer`.

- [ ] **Step 3: Implement**

Create `packages/core/src/shannon_core/display/rich_renderer.py`:

```python
"""RichConsoleRenderer — renders DisplayEvents to Rich live terminal output.

Uses Panel for per-agent grouping, Progress for parallel agent tracking, and
a category->style map for consistent color semantics.
"""
from __future__ import annotations

from rich.console import Console

from shannon_core.display.formatters import agent_prefix


class RichConsoleRenderer:
    STYLE_MAP = {
        "PHASE": "bold cyan",
        "AGENT": "blue",
        "TOOL": "yellow",
        "LLM": "magenta",
        "ERROR": "bold red",
        "RESUME": "dim yellow",
    }

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    async def render(self, event) -> None:
        from shannon_core.display.events import (
            AgentEvent, ErrorEvent, LlmTurnEvent, PhaseEvent,
            ResumeEvent, SummaryEvent, ToolCallEvent, WorkflowHeader,
        )
        match event:
            case WorkflowHeader(): self._render_header(event)
            case PhaseEvent(): self._render_phase(event)
            case AgentEvent(): self._render_agent(event)
            case ToolCallEvent(): self._render_tool(event)
            case LlmTurnEvent(): self._render_llm(event)
            case ErrorEvent(): self._render_error(event)
            case SummaryEvent(): self._render_summary(event)
            case ResumeEvent(): self._render_resume(event)

    def _render_header(self, e) -> None:
        from rich.panel import Panel
        body = (
            f"Workflow:  {e.workflow_id or 'N/A'}\n"
            f"Target:    {e.target_url or 'N/A'}\n"
            f"Started:   {e.timestamp}"
        )
        self._console.print(Panel(body, title="Shannon Pentest", border_style="cyan"))

    def _render_phase(self, e) -> None:
        verb = "Starting" if e.event == "start" else "Completed"
        self._console.print(
            f"[{e.timestamp}] [bold cyan]PHASE[/]  {verb} {e.phase} {'─' * 20}",
            highlight=False,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/rich_renderer.py packages/core/tests/display/test_rich_renderer.py
git commit -m "feat(display): RichConsoleRenderer header and phase rendering"
```

---

### Task 15: RichConsoleRenderer — agent panel + tool + llm

**Files:**
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py`
- Modify: `packages/core/tests/display/test_rich_renderer.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/display/test_rich_renderer.py`:

```python
from shannon_core.display.events import AgentEvent, LlmTurnEvent, ToolCallEvent


async def test_agent_start_shows_prefix():
    renderer, _ = _renderer_with_capture()
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="injection-vuln",
        event="start", attempt=1))
    out = renderer._console.export_text()
    assert "Injection" in out
    assert "injection-vuln" in out


async def test_agent_end_completed_shows_metrics():
    renderer, _ = _renderer_with_capture()
    await renderer.render(AgentEvent(
        timestamp="t", category="AGENT", agent_name="xss-vuln",
        event="end", attempt=1, duration_ms=5200, cost_usd=0.15, success=True))
    out = renderer._console.export_text()
    assert "Completed" in out
    assert "5.2s" in out
    assert "0.15" in out


async def test_tool_renders_humanized():
    renderer, _ = _renderer_with_capture()
    await renderer.render(ToolCallEvent(
        timestamp="t", category="TOOL", agent_name="injection-vuln",
        tool_name="Bash", parameters={"command": "ls"}))
    out = renderer._console.export_text()
    assert "Bash" in out
    assert "command=ls" in out


async def test_llm_renders_turn():
    renderer, _ = _renderer_with_capture()
    await renderer.render(LlmTurnEvent(
        timestamp="t", category="LLM", agent_name="injection-vuln",
        turn=1, content="Analyzing code"))
    out = renderer._console.export_text()
    assert "Turn 1" in out
    assert "Analyzing code" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py -k "agent or tool or llm" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add these imports to the top of `packages/core/src/shannon_core/display/rich_renderer.py`:

```python
from rich.panel import Panel

from shannon_core.display.formatters import (
    agent_prefix, format_duration, humanize_tool_call,
)
```

(Remove the standalone `from shannon_core.display.formatters import agent_prefix` added earlier, replacing with the combined import above.)

Then add these methods to the `RichConsoleRenderer` class:

```python
    def _agent_panel_title(self, agent_name: str) -> str:
        pfx = agent_prefix(agent_name)
        if pfx == "[Agent]":
            return agent_name
        return f"{pfx} {agent_name}"

    def _render_agent(self, e) -> None:
        title = self._agent_panel_title(e.agent_name)
        if e.event == "start":
            self._console.print(f"[{e.timestamp}] [blue]AGENT[/]  ▶ {title} started (attempt {e.attempt})")
            return
        # end
        if e.success is False:
            dur = format_duration(e.duration_ms) if e.duration_ms is not None else "?"
            self._console.print(f"[red]{title} failed ({dur}) — {e.error or ''}[/]")
            return
        parts = []
        if e.duration_ms is not None:
            parts.append(format_duration(e.duration_ms))
        if e.cost_usd is not None:
            parts.append(f"${e.cost_usd:.4f}")
        metrics = f" ({', '.join(parts)})" if parts else ""
        self._console.print(f"[green]{title} completed{metrics}[/]")

    def _render_tool(self, e) -> None:
        params = humanize_tool_call(e.tool_name, e.parameters if isinstance(e.parameters, dict) else {})
        self._console.print(f"[{e.timestamp}] [yellow]🔧 {e.tool_name}({params})[/]", highlight=False)

    def _render_llm(self, e) -> None:
        content = e.content[:200] + "..." if len(e.content) > 200 else e.content
        self._console.print(f"[{e.timestamp}] [magenta]💭 Turn {e.turn}: {content}[/]", highlight=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/rich_renderer.py packages/core/tests/display/test_rich_renderer.py
git commit -m "feat(display): RichConsoleRenderer agent/tool/llm with prefixes and icons"
```

---

### Task 16: RichConsoleRenderer — error + summary + resume

**Files:**
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py`
- Modify: `packages/core/tests/display/test_rich_renderer.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/display/test_rich_renderer.py`:

```python
from shannon_core.display.events import AgentMetric, ErrorEvent, ResumeEvent, SummaryEvent


async def test_error_renders_in_red_with_classification():
    renderer, _ = _renderer_with_capture()
    await renderer.render(ErrorEvent(
        timestamp="t", category="ERROR", error_type="RuntimeError", message="boom",
        classified="BillingError", display_retryable=True))
    out = renderer._console.export_text()
    assert "RuntimeError" in out
    assert "boom" in out
    assert "BillingError" in out


async def test_summary_completed_renders_panel():
    renderer, _ = _renderer_with_capture()
    await renderer.render(SummaryEvent(
        timestamp="t", category="SUMMARY", status="completed",
        total_duration_ms=12400, total_cost_usd=0.3450,
        agents=[AgentMetric(name="xss-vuln", duration_ms=4100, cost_usd=0.165)]))
    out = renderer._console.export_text()
    assert "COMPLETED" in out
    assert "12.4s" in out
    assert "xss-vuln" in out


async def test_resume_renders_message():
    renderer, _ = _renderer_with_capture()
    await renderer.render(ResumeEvent(
        timestamp="t", category="RESUME", previous_workflow_id="w1",
        new_workflow_id="w2", checkpoint_hash="abc", completed_agents=["a"]))
    out = renderer._console.export_text()
    assert "Resuming" in out
    assert "w2" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py -k "error or summary or resume" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add these methods to the `RichConsoleRenderer` class:

```python
    def _render_error(self, e) -> str:
        line = f"[{e.timestamp}] [bold red]ERROR[/]  {e.error_type}: {e.message}"
        if e.context:
            line += f" (context: {e.context})"
        if e.classified:
            flag = "retryable" if e.display_retryable else "non-retryable"
            line += f" [{e.classified} · {flag}]"
        self._console.print(line, highlight=False)

    def _render_summary(self, e) -> None:
        from rich.table import Table
        status = e.status.upper()
        self._console.print(Panel.fit(
            f"Workflow [bold]{status}[/]\n"
            f"Duration: {format_duration(e.total_duration_ms)}    "
            f"Total Cost: ${e.total_cost_usd:.4f}",
            border_style="green" if e.status == "completed" else "red",
        ))
        if e.agents:
            table = Table(show_header=True, header_style="bold")
            table.add_column("Status")
            table.add_column("Agent")
            table.add_column("Duration")
            table.add_column("Cost")
            for m in e.agents:
                mark = "✓" if m.success else "✗"
                cost = f"${m.cost_usd:.4f}" if m.cost_usd is not None else "—"
                table.add_row(mark, m.name, format_duration(m.duration_ms), cost)
            self._console.print(table)
        if e.error:
            self._console.print(f"[red]{format_error_block(e.error)}[/]", highlight=False)

    def _render_resume(self, e) -> None:
        self._console.print(
            f"[dim yellow][{e.timestamp}] [RESUME] Resuming workflow[/]\n"
            f"  Previous: {e.previous_workflow_id}    New: {e.new_workflow_id}",
            highlight=False,
        )
```

Add `format_error_block` to the formatters import at the top:

```python
from shannon_core.display.formatters import (
    agent_prefix, format_duration, format_error_block, humanize_tool_call,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/display/test_rich_renderer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/display/rich_renderer.py packages/core/tests/display/test_rich_renderer.py
git commit -m "feat(display): RichConsoleRenderer error/summary/resume with Panel and Table"
```

---

### Task 17: Integrate into WorkflowLogger (emit events)

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py`
- Modify: `packages/whitebox/tests/test_workflow_logger.py`

The `WorkflowLogger` now constructs `DisplayEvent`s and dispatches them. It builds a `DisplayDispatcher` containing a `FileLogRenderer` wrapping the existing `LogStream` (satisfies `LineWriter`). The `RichConsoleRenderer` is optional and off by default (controlled by a constructor flag) to keep existing CLI behavior unchanged until explicitly enabled.

- [ ] **Step 1: Read existing test to understand current expectations**

Run: `sed -n '1,40p' packages/whitebox/tests/test_workflow_logger.py`
Expected: shows `_make_meta`, `_audit_dir`, `_read_log` helpers and `test_initialize_creates_workflow_log` asserting on workflow.log content.

- [ ] **Step 2: Add a new test for event-based rendering**

Append to `packages/whitebox/tests/test_workflow_logger.py`:

```python
async def test_log_phase_uses_starting_verb(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize(workflow_id="wf-1")
    await logger.log_phase("reconnaissance", "start")
    content = _read_log(tmp_path)
    # New format: "Starting reconnaissance" (was "reconnaissance started")
    assert "[PHASE] Starting reconnaissance" in content
    await logger.close()


async def test_log_agent_end_includes_prefix_and_cost(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize(workflow_id="wf-1")
    from shannon_core.models.audit import AgentLogDetails
    await logger.log_agent("xss-vuln", "end", AgentLogDetails(
        attempt_number=1, duration_ms=5200, cost_usd=0.15, success=True))
    content = _read_log(tmp_path)
    assert "[AGENT] [XSS] xss-vuln: Completed (5.2s, $0.1500)" in content
    await logger.close()
```

- [ ] **Step 3: Run new tests to verify they fail**

Run: `uv run pytest packages/whitebox/tests/test_workflow_logger.py::test_log_phase_uses_starting_verb packages/whitebox/tests/test_workflow_logger.py::test_log_agent_end_includes_prefix_and_cost -v`
Expected: FAIL — current WorkflowLogger emits "reconnaissance started" / "ended (...)" without prefix.

- [ ] **Step 4: Rewrite WorkflowLogger to emit events**

Replace the body of `packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py` with:

```python
from typing import Any, Literal

from shannon_core.display.dispatcher import DisplayDispatcher
from shannon_core.display.events import (
    AgentEvent, AgentMetric, ErrorEvent, LlmTurnEvent, PhaseEvent,
    ResumeEvent, SummaryEvent, ToolCallEvent, WorkflowHeader,
)
from shannon_core.display.file_renderer import FileLogRenderer
from shannon_core.display.formatters import format_log_time
from shannon_core.models.audit import AgentLogDetails, ResumeInfo, WorkflowSummary
from shannon_core.models.metrics import SessionMetadata
from shannon_whitebox.audit.log_stream import LogStream
from shannon_whitebox.audit.utils import generate_workflow_log_path


class WorkflowLogger:
    """Emits DisplayEvents through a dispatcher.

    The dispatcher fans events to FileLogRenderer (writes workflow.log via the
    injected LogStream) and, optionally, a RichConsoleRenderer for live stdout.
    """

    def __init__(self, session_metadata: SessionMetadata, use_rich: bool = False) -> None:
        self._meta = session_metadata
        self._workflow_id: str | None = None
        self._stream: LogStream | None = None
        self._dispatcher: DisplayDispatcher | None = None
        self._use_rich = use_rich

    async def initialize(self, workflow_id: str | None = None) -> None:
        self._workflow_id = workflow_id
        path = generate_workflow_log_path(self._meta)
        self._stream = LogStream(path)
        await self._stream.open()

        renderers: list = [FileLogRenderer(self._stream)]
        if self._use_rich:
            from shannon_core.display.rich_renderer import RichConsoleRenderer
            renderers.append(RichConsoleRenderer())
        self._dispatcher = DisplayDispatcher(renderers)

        await self._dispatcher.dispatch(WorkflowHeader(
            timestamp=format_log_time(), category="HEADER",
            workflow_id=workflow_id, target_url=self._meta.web_url,
        ))

    async def log_phase(self, phase: str, event: Literal["start", "complete"]) -> None:
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(PhaseEvent(
            timestamp=format_log_time(), category="PHASE", phase=phase, event=event))

    async def log_agent(self, agent_name: str, event: Literal["start", "end"],
                        details: AgentLogDetails | None = None) -> None:
        if self._dispatcher is None:
            return
        d = details or AgentLogDetails(attempt_number=1)
        await self._dispatcher.dispatch(AgentEvent(
            timestamp=format_log_time(), category="AGENT", agent_name=agent_name,
            event=event, attempt=d.attempt_number, duration_ms=d.duration_ms,
            cost_usd=d.cost_usd, success=d.success, error=d.error))

    async def log_tool_start(self, agent_name: str, tool_name: str, parameters: Any) -> None:
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(ToolCallEvent(
            timestamp=format_log_time(), category="TOOL", agent_name=agent_name,
            tool_name=tool_name, parameters=parameters))

    async def log_llm_response(self, agent_name: str, turn: int, content: str) -> None:
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(LlmTurnEvent(
            timestamp=format_log_time(), category="LLM", agent_name=agent_name,
            turn=turn, content=content))

    async def log_event(self, event_type: str, message: str) -> None:
        if self._dispatcher is None:
            return
        # Generic event written directly (rare; not worth a dedicated event type).
        await self._stream.write(f"[{format_log_time()}] [{event_type}] {message}\n")

    async def log_error(self, error: Exception, context: str | None = None) -> None:
        if self._dispatcher is None:
            return
        from shannon_core.errors.classification import classify_for_temporal, is_retryable_for_display
        etype, _ = classify_for_temporal(error)
        await self._dispatcher.dispatch(ErrorEvent(
            timestamp=format_log_time(), category="ERROR",
            error_type=type(error).__name__, message=str(error), context=context,
            classified=etype.value, display_retryable=is_retryable_for_display(error)))

    async def log_workflow_complete(self, summary: WorkflowSummary) -> None:
        if self._dispatcher is None:
            return
        agents = [
            AgentMetric(name=n, duration_ms=m.duration_ms, cost_usd=m.cost_usd, success=True)
            for n, m in summary.agent_metrics.items()
        ]
        await self._dispatcher.dispatch(SummaryEvent(
            timestamp=format_log_time(), category="SUMMARY", status=summary.status,
            total_duration_ms=summary.total_duration_ms, total_cost_usd=summary.total_cost_usd,
            agents=agents, error=summary.error))

    async def log_resume_header(self, resume_info: ResumeInfo) -> None:
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(ResumeEvent(
            timestamp=format_log_time(), category="RESUME",
            previous_workflow_id=resume_info.previous_workflow_id,
            new_workflow_id=resume_info.new_workflow_id,
            checkpoint_hash=resume_info.checkpoint_hash,
            completed_agents=resume_info.completed_agents))

    async def close(self) -> None:
        if self._stream is not None:
            await self._stream.close()
            self._stream = None
        self._dispatcher = None  # all log_* methods check dispatcher and no-op after close
```

- [ ] **Step 5: Update existing tests that assert old format**

Seven assertions in `test_workflow_logger.py` check the OLD format and must be updated to the new `FileLogRenderer` output. Make these exact edits:

Edit 1 — `test_log_phase` (phase verbs):
```python
# OLD:
    assert "[PHASE] recon started" in content
    assert "[PHASE] recon completed" in content
# NEW:
    assert "[PHASE] Starting recon" in content
    assert "[PHASE] Completed recon" in content
```

Edit 2 — `test_log_agent_start` (agent start verb):
```python
# OLD:
    assert "[AGENT] recon started" in content
# NEW:
    assert "[AGENT] recon: Starting" in content
```

Edit 3 — `test_log_agent_end_with_details` (agent end verb; metrics assertions stay):
```python
# OLD:
    assert "[AGENT] recon ended" in content
# NEW:
    assert "[AGENT] recon: Completed" in content
```
(`assert "2m 30s" in content` and `assert "$0.0500" in content` remain unchanged — they still pass.)

Edit 4 — `test_log_agent_end_with_error` (failed-agent phrasing):
```python
# OLD:
    assert "error: Rate limit exceeded" in content
# NEW:
    assert "Failed" in content
    assert "Rate limit exceeded" in content
```

Edit 5 — `test_log_tool_start` (tool line separator):
```python
# OLD:
    assert "[TOOL] recon → Read(" in content
# NEW:
    assert "[TOOL]  recon: Read:" in content
```
(`assert "file_path=/etc/passwd" in content` stays — still passes.)

Edit 6 — `test_log_llm_response` (LLM line capitalization):
```python
# OLD:
    assert "[LLM] recon turn 1:" in content
# NEW:
    assert "[LLM]   recon: Turn 1:" in content
```

After these 6 edits, run:

Run: `uv run pytest packages/whitebox/tests/test_workflow_logger.py -v`
Expected: PASS — all tests (including the two new tests from Step 2) pass. The remaining existing tests (`test_initialize_*`, `test_log_error*`, `test_log_workflow_complete*`, `test_log_resume_header`, `test_close_prevents_further_writes`, `test_log_tool_start_truncates_long_bash_command`, `test_log_llm_response_truncates_long_content`, `test_log_event`) need NO changes — their assertions are satisfied by the new implementation.

> **Why `recon` has no `[Prefix]`:** `recon` is not a vuln/exploit agent, so `agent_prefix("recon")` returns `"[Agent]"`, and `_prefixed()` renders it as bare `recon`. Only `*-vuln`/`*-exploit` agents get a prefix. This is why the existing `recon`-based tests only need verb changes, not prefix additions.

- [ ] **Step 6: Run full test suite to verify no regressions**

Run: `uv run pytest -v`
Expected: ALL tests pass (core display/errors tests + whitebox tests including updated workflow_logger tests).

- [ ] **Step 7: Manual smoke test of workflow.log format**

Run: `uv run python -c "
import asyncio
from pathlib import Path
import tempfile
from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentLogDetails, WorkflowSummary
from shannon_whitebox.audit.workflow_logger import WorkflowLogger
from shannon_whitebox.audit.utils import generate_audit_path

async def main():
    d = tempfile.mkdtemp()
    meta = SessionMetadata(id='smoke', web_url='https://example.com', output_path=d)
    logger = WorkflowLogger(meta)
    await logger.initialize(workflow_id='wf-smoke')
    await logger.log_phase('reconnaissance', 'start')
    await logger.log_agent('injection-vuln', 'start', AgentLogDetails(attempt_number=1))
    await logger.log_tool_start('injection-vuln', 'Bash', {'command': 'ls -la'})
    await logger.log_agent('injection-vuln', 'end', AgentLogDetails(attempt_number=1, duration_ms=5200, cost_usd=0.15, success=True))
    await logger.close()
    print(Path(generate_audit_path(meta)) .joinpath('workflow.log').read_text())

asyncio.run(main())
"`
Expected: prints a workflow.log containing `[PHASE] Starting reconnaissance`, `[AGENT] [Injection] injection-vuln: Starting`, `[TOOL]  [Injection] Bash: command=ls -la`, and `[AGENT] [Injection] injection-vuln: Completed (5.2s, $0.1500)`.

- [ ] **Step 8: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py packages/whitebox/tests/test_workflow_logger.py
git commit -m "refactor(whitebox): WorkflowLogger emits DisplayEvents via dispatcher"
```

---

## Self-Review (run after writing the plan)

### Spec coverage

| Spec section | Implementing task(s) |
|---|---|
| Part 1 — Event model | Task 3 |
| Part 2 — RichConsoleRenderer (Panel/Progress/color, agent prefix) | Tasks 14, 15, 16 |
| Part 3 — FileLogRenderer (plain text, COMPLETION_PATTERN compat) | Tasks 9, 10, 11 (+ compat check in 11) |
| Part 4.1 — agent_prefix, humanize_tool_call, summarize_todo, maybe_browser_action, format_error_block | Tasks 6, 7, 8 |
| Part 4.2 — classify_for_temporal + is_retryable_for_display (opposite fallbacks) | Tasks 12, 13 |
| Part 4.3 —超越 (surpass) enhancements | Event model (3), Rich Panel/Progress (14-16), classification module (12-13) |
| Part 5.1 — Dispatcher + integration | Tasks 4, 17 |
| Part 5.2 — File structure (display/, errors/) | All tasks |
| Part 5.3 — Testing (events, formatters, classification, renderers, compat) | Tasks 3, 5-8, 9-13, 14-16, 17 |
| Gap LOG-A1 tool-call humanization | Task 8 |
| Gap LOG-A2/LOG-D4 agent prefix | Tasks 6, 10, 15 |
| Gap LOG-A6/LOG-D7 error classification | Tasks 12, 13, 11, 16 |
| Gap LOG-D2 PHASE verb unification | Tasks 9, 17 |
| Gap LOG-D6 Task/TodoWrite display | Task 8 |
| Gap LOG-D1 spinner (Rich Progress) | Task 16 (summary Table; full Progress requires runtime wiring, noted below) |

### Notes / known simplifications
- **Rich `Progress` (live spinner) for in-flight agents**: Task 16 renders the summary as a Table. A live `Progress` bar that updates during agent execution requires a `Live` context wired into the workflow runner (not just the renderer), which is a separate integration concern. The renderer exposes the hooks (`_render_agent` start/end), and a follow-up can add `Live` to the whitebox workflow runner. This is called out so it is not mistaken for an omission.
- **`log_event` generic path**: kept as a direct stream write to preserve the current generic-category behavior without inventing a new event type — minimal change, YAGNI.
- **`format_error_block` import**: Task 11 adds it to `file_renderer.py` imports; Task 16 adds it to `rich_renderer.py` imports. Both are explicit.

### Placeholder scan
No "TBD", "TODO", or "implement later". Every code step contains full code. Type names are consistent: `DisplayDispatcher.dispatch`, `FileLogRenderer.render`, `RichConsoleRenderer.render`, `agent_prefix`, `humanize_tool_call`, `classify_for_temporal`, `is_retryable_for_display`, `ErrorType` — all defined before/where used.

### Type consistency
- `AgentMetric` (defined Task 3) used in `SummaryEvent.agents` (Task 3), `FileLogRenderer._summary` (Task 11), `RichConsoleRenderer._render_summary` (Task 16). ✓
- `LineWriter` Protocol (Task 2) satisfied by `LogStream.write` (async, str). ✓
- `DisplayDispatcher.dispatch` (Task 4) called in `WorkflowLogger` (Task 17). ✓
- `classify_for_temporal` returns `tuple[ErrorType, bool]` (Task 12); used in `WorkflowLogger.log_error` (Task 17) as `etype.value`. ✓
