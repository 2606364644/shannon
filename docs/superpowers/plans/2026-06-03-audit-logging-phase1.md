# Audit Logging Phase 1 — Core Class Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the Python audit package to match the original Shannon (TypeScript) layered architecture — `AgentLogger`, `WorkflowLogger`, `MetricsTracker` coordinated by a simplified `AuditSession` facade — without yet integrating it into the pipeline.

**Architecture:** Three specialized components behind a `AuditSession` facade. `AgentLogger` writes JSON Lines to per-agent log files. `WorkflowLogger` writes human-readable structured logs. `MetricsTracker` manages `session.json` with atomic writes. All async, using `aiofiles` and `asyncio.Lock` for concurrency safety. Pure utility functions (`utils.py`) for path generation and formatting.

**Tech Stack:** Python 3.12+, Pydantic v2, aiofiles, pytest + pytest-asyncio (auto mode)

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `packages/core/src/shannon_core/models/audit.py` | Data types: `AgentEndResult`, `AgentLogDetails`, `AgentMetricsSummary`, `WorkflowSummary`, `ResumeInfo` |
| Modify | `packages/core/src/shannon_core/models/metrics.py:14` | Make `SessionMetadata.web_url` optional |
| Modify | `packages/core/src/shannon_core/models/__init__.py` | Export new audit types |
| Create | `packages/whitebox/src/shannon_whitebox/audit/utils.py` | Path generation + formatting utilities |
| Modify | `packages/whitebox/src/shannon_whitebox/audit/log_stream.py` | Add open/close lifecycle, keep backward-compat `append()` |
| Create | `packages/whitebox/src/shannon_whitebox/audit/agent_logger.py` | JSON Lines agent log with header |
| Create | `packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py` | Human-readable workflow log with categories |
| Create | `packages/whitebox/src/shannon_whitebox/audit/metrics_tracker.py` | session.json management with atomic read/write |
| Modify | `packages/whitebox/src/shannon_whitebox/audit/session.py` | Simplified facade coordinating three components |
| Modify | `packages/whitebox/src/shannon_whitebox/audit/__init__.py` | Export `AuditSession` |
| Create | `packages/whitebox/tests/test_audit_utils.py` | Tests for utils |
| Create | `packages/whitebox/tests/test_log_stream.py` | Tests for enhanced LogStream |
| Create | `packages/whitebox/tests/test_agent_logger.py` | Tests for AgentLogger |
| Create | `packages/whitebox/tests/test_workflow_logger.py` | Tests for WorkflowLogger |
| Create | `packages/whitebox/tests/test_metrics_tracker.py` | Tests for MetricsTracker |
| Create | `packages/whitebox/tests/test_audit_session.py` | Tests for AuditSession facade |
| Modify | `packages/core/tests/test_metrics.py:26,33` | Update tests for optional `web_url` |

---

### Task 1: Audit Data Types

**Files:**
- Create: `packages/core/src/shannon_core/models/audit.py`
- Modify: `packages/core/src/shannon_core/models/metrics.py`
- Modify: `packages/core/src/shannon_core/models/__init__.py`
- Modify: `packages/core/tests/test_metrics.py`
- Test: `packages/core/tests/test_metrics.py`

- [ ] **Step 1: Write failing test for optional `web_url`**

Add to `packages/core/tests/test_metrics.py`:

```python
def test_session_metadata_optional_web_url():
    s = SessionMetadata(id="test-123")
    assert s.id == "test-123"
    assert s.web_url is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_metrics.py::test_session_metadata_optional_web_url -v`
Expected: FAIL — `web_url` is required

- [ ] **Step 3: Make `web_url` optional in `SessionMetadata`**

In `packages/core/src/shannon_core/models/metrics.py`, change line 17 from:
```python
    web_url: str
```
to:
```python
    web_url: str | None = None
```

- [ ] **Step 4: Run the full metrics test suite to confirm no regressions**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_metrics.py -v`
Expected: All PASS (existing tests still pass because they still provide `web_url`)

- [ ] **Step 5: Write failing test for new audit data types**

Create `packages/core/tests/test_audit_types.py`:

```python
from shannon_core.models.audit import (
    AgentEndResult,
    AgentLogDetails,
    AgentMetricsSummary,
    WorkflowSummary,
    ResumeInfo,
)


def test_agent_end_result_defaults():
    r = AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05)
    assert r.success is True
    assert r.duration_ms == 5000
    assert r.cost_usd == 0.05
    assert r.attempt_number == 1
    assert r.model is None
    assert r.error is None
    assert r.is_final_attempt is True
    assert r.checkpoint is None


def test_agent_end_result_with_error():
    r = AgentEndResult(
        success=False,
        duration_ms=1000,
        cost_usd=0.01,
        attempt_number=3,
        error="Rate limit exceeded",
        is_final_attempt=False,
    )
    assert r.error == "Rate limit exceeded"
    assert r.is_final_attempt is False
    assert r.attempt_number == 3


def test_agent_log_details_defaults():
    d = AgentLogDetails()
    assert d.attempt_number == 1
    assert d.duration_ms is None
    assert d.cost_usd is None
    assert d.success is None
    assert d.error is None


def test_agent_metrics_summary():
    m = AgentMetricsSummary(duration_ms=30000, cost_usd=0.05)
    assert m.duration_ms == 30000
    assert m.cost_usd == 0.05


def test_agent_metrics_summary_no_cost():
    m = AgentMetricsSummary(duration_ms=1000)
    assert m.cost_usd is None


def test_workflow_summary_completed():
    s = WorkflowSummary(
        status="completed",
        total_duration_ms=300000,
        total_cost_usd=0.12,
        completed_agents=["recon", "injection-vuln"],
        agent_metrics={
            "recon": AgentMetricsSummary(duration_ms=150000, cost_usd=0.06),
            "injection-vuln": AgentMetricsSummary(duration_ms=150000, cost_usd=0.06),
        },
    )
    assert s.status == "completed"
    assert len(s.completed_agents) == 2
    assert s.error is None


def test_workflow_summary_failed_with_error():
    s = WorkflowSummary(
        status="failed",
        total_duration_ms=10000,
        total_cost_usd=0.01,
        completed_agents=[],
        agent_metrics={},
        error="Agent crashed",
    )
    assert s.status == "failed"
    assert s.error == "Agent crashed"


def test_resume_info():
    r = ResumeInfo(
        previous_workflow_id="wf-old",
        new_workflow_id="wf-new",
        checkpoint_hash="abc123",
        completed_agents=["recon"],
    )
    assert r.previous_workflow_id == "wf-old"
    assert r.completed_agents == ["recon"]
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_audit_types.py -v`
Expected: FAIL — `shannon_core.models.audit` module not found

- [ ] **Step 7: Create the audit types module**

Create `packages/core/src/shannon_core/models/audit.py`:

```python
from typing import Literal

from pydantic import BaseModel


class AgentEndResult(BaseModel):
    success: bool
    duration_ms: int
    cost_usd: float
    attempt_number: int = 1
    model: str | None = None
    error: str | None = None
    is_final_attempt: bool = True
    checkpoint: str | None = None


class AgentLogDetails(BaseModel):
    attempt_number: int = 1
    duration_ms: int | None = None
    cost_usd: float | None = None
    success: bool | None = None
    error: str | None = None


class AgentMetricsSummary(BaseModel):
    duration_ms: int
    cost_usd: float | None = None


class WorkflowSummary(BaseModel):
    status: Literal["completed", "failed", "cancelled"]
    total_duration_ms: int
    total_cost_usd: float
    completed_agents: list[str]
    agent_metrics: dict[str, AgentMetricsSummary]
    error: str | None = None


class ResumeInfo(BaseModel):
    previous_workflow_id: str
    new_workflow_id: str
    checkpoint_hash: str
    completed_agents: list[str]
```

- [ ] **Step 8: Update core models `__init__.py` to export new types**

In `packages/core/src/shannon_core/models/__init__.py`, add after the `from .metrics import` line:

```python
from .audit import (
    AgentEndResult,
    AgentLogDetails,
    AgentMetricsSummary,
    ResumeInfo,
    WorkflowSummary,
)
```

Add to the `__all__` list:

```python
    "AgentEndResult",
    "AgentLogDetails",
    "AgentMetricsSummary",
    "ResumeInfo",
    "WorkflowSummary",
```

- [ ] **Step 9: Run all tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_audit_types.py packages/core/tests/test_metrics.py -v`
Expected: All PASS

- [ ] **Step 10: Commit**

```bash
git add packages/core/src/shannon_core/models/audit.py packages/core/src/shannon_core/models/__init__.py packages/core/src/shannon_core/models/metrics.py packages/core/tests/test_audit_types.py packages/core/tests/test_metrics.py
git commit -m "feat(core): add audit data types and make SessionMetadata.web_url optional"
```

---

### Task 2: Utility Functions (utils.py)

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/audit/utils.py`
- Test: `packages/whitebox/tests/test_audit_utils.py`

- [ ] **Step 1: Write failing tests for utils**

Create `packages/whitebox/tests/test_audit_utils.py`:

```python
import time
from pathlib import Path

from shannon_core.models.metrics import SessionMetadata
from shannon_whitebox.audit.utils import (
    format_duration,
    format_timestamp,
    format_log_time,
    sanitize_hostname,
    generate_audit_path,
    generate_log_path,
    generate_prompt_path,
    generate_workflow_log_path,
    generate_session_json_path,
    initialize_audit_structure,
)


# --- format_duration ---

def test_format_duration_milliseconds():
    assert format_duration(23) == "23ms"


def test_format_duration_sub_second():
    assert format_duration(500) == "500.0ms"


def test_format_duration_seconds():
    assert format_duration(1500) == "1.5s"


def test_format_duration_minutes_and_seconds():
    assert format_duration(150000) == "2m 30s"


def test_format_duration_exact_minute():
    assert format_duration(60000) == "1m 0s"


def test_format_duration_zero():
    assert format_duration(0) == "0ms"


# --- format_timestamp ---

def test_format_timestamp_returns_iso_string():
    ts = format_timestamp()
    assert ts.endswith("Z")
    assert "T" in ts


def test_format_timestamp_from_float():
    # 2026-01-01 00:00:00 UTC
    ts = format_timestamp(1735689600.0)
    assert ts.startswith("2026-01-01T")
    assert ts.endswith("Z")


# --- format_log_time ---

def test_format_log_time_format():
    lt = format_log_time()
    # Should match YYYY-MM-DD HH:MM:SS
    parts = lt.split(" ")
    assert len(parts) == 2
    date_part = parts[0]
    time_part = parts[1]
    assert len(date_part) == 10
    assert len(time_part) == 8
    assert date_part[4] == "-"
    assert time_part[2] == ":"


# --- sanitize_hostname ---

def test_sanitize_hostname_https():
    assert sanitize_hostname("https://example.com") == "example-com"


def test_sanitize_hostname_http():
    assert sanitize_hostname("http://test.example.com/path") == "test-example-com"


def test_sanitize_hostname_with_port():
    assert sanitize_hostname("https://localhost:3000") == "localhost-3000"


# --- path generation ---

def _make_meta(**kwargs) -> SessionMetadata:
    defaults = {"id": "test-session", "output_path": None}
    defaults.update(kwargs)
    return SessionMetadata(**defaults)


def test_generate_audit_path_with_output_path():
    meta = _make_meta(output_path="/tmp/workspaces")
    assert generate_audit_path(meta) == Path("/tmp/workspaces/test-session")


def test_generate_audit_path_without_output_path():
    meta = _make_meta(output_path=None)
    assert generate_audit_path(meta) == Path("workspaces/test-session")


def test_generate_log_path():
    meta = _make_meta(output_path="/tmp/ws")
    path = generate_log_path(meta, "recon", 1700000000, 1)
    assert path == Path("/tmp/ws/agents/1700000000_recon_attempt-1.log")


def test_generate_prompt_path():
    meta = _make_meta(output_path="/tmp/ws")
    path = generate_prompt_path(meta, "recon")
    assert path == Path("/tmp/ws/prompts/recon.md")


def test_generate_workflow_log_path():
    meta = _make_meta(output_path="/tmp/ws")
    path = generate_workflow_log_path(meta)
    assert path == Path("/tmp/ws/workflow.log")


def test_generate_session_json_path():
    meta = _make_meta(output_path="/tmp/ws")
    path = generate_session_json_path(meta)
    assert path == Path("/tmp/ws/session.json")


def test_initialize_audit_structure(tmp_path):
    meta = _make_meta(output_path=str(tmp_path / "ws"))
    initialize_audit_structure(meta)
    base = tmp_path / "ws"
    assert (base / "agents").is_dir()
    assert (base / "prompts").is_dir()
    assert (base / "deliverables").is_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_audit_utils.py -v`
Expected: FAIL — `shannon_whitebox.audit.utils` module not found

- [ ] **Step 3: Implement utils.py**

Create `packages/whitebox/src/shannon_whitebox/audit/utils.py`:

```python
import os
from datetime import datetime, timezone
from pathlib import Path

from shannon_core.models.metrics import SessionMetadata


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
    """Human-readable local format 'YYYY-MM-DD HH:MM:SS' for workflow.log lines."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sanitize_hostname(url: str) -> str:
    """Extract and sanitize hostname from URL for use as a directory-safe identifier."""
    hostname = url.replace("https://", "").replace("http://", "").split("/")[0]
    return hostname.replace(".", "-").replace(":", "-")


def generate_audit_path(meta: SessionMetadata) -> Path:
    """Root directory for a session's audit artifacts."""
    if meta.output_path:
        base = Path(meta.output_path)
    else:
        base = Path("workspaces")
    return base / meta.id


def generate_log_path(meta: SessionMetadata, agent_name: str, timestamp: int, attempt: int) -> Path:
    """Path to an agent's JSON Lines log file."""
    return generate_audit_path(meta) / "agents" / f"{timestamp}_{agent_name}_attempt-{attempt}.log"


def generate_prompt_path(meta: SessionMetadata, agent_name: str) -> Path:
    """Path to an agent's prompt snapshot markdown file."""
    return generate_audit_path(meta) / "prompts" / f"{agent_name}.md"


def generate_workflow_log_path(meta: SessionMetadata) -> Path:
    """Path to the human-readable workflow log."""
    return generate_audit_path(meta) / "workflow.log"


def generate_session_json_path(meta: SessionMetadata) -> Path:
    """Path to the session.json metrics file."""
    return generate_audit_path(meta) / "session.json"


def initialize_audit_structure(meta: SessionMetadata) -> None:
    """Create the directory structure for a session's audit artifacts."""
    base = generate_audit_path(meta)
    (base / "agents").mkdir(parents=True, exist_ok=True)
    (base / "prompts").mkdir(parents=True, exist_ok=True)
    (base / "deliverables").mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_audit_utils.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/utils.py packages/whitebox/tests/test_audit_utils.py
git commit -m "feat(whitebox): add audit path generation and formatting utilities"
```

---

### Task 3: Enhanced LogStream

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/audit/log_stream.py`
- Test: `packages/whitebox/tests/test_log_stream.py`

- [ ] **Step 1: Write failing tests for enhanced LogStream**

Create `packages/whitebox/tests/test_log_stream.py`:

```python
from pathlib import Path

import pytest

from shannon_whitebox.audit.log_stream import LogStream


async def test_open_creates_file(tmp_path: Path):
    stream = LogStream(tmp_path / "subdir" / "test.log")
    await stream.open()
    assert stream.is_open
    assert stream.path == tmp_path / "subdir" / "test.log"
    assert (tmp_path / "subdir" / "test.log").exists()
    await stream.close()


async def test_open_creates_parent_directories(tmp_path: Path):
    stream = LogStream(tmp_path / "deep" / "nested" / "dir" / "file.log")
    await stream.open()
    assert (tmp_path / "deep" / "nested" / "dir").is_dir()
    await stream.close()


async def test_write_raw_text(tmp_path: Path):
    stream = LogStream(tmp_path / "test.log")
    await stream.open()
    await stream.write("hello world\n")
    await stream.close()
    content = (tmp_path / "test.log").read_text()
    assert content == "hello world\n"


async def test_write_multiple_times(tmp_path: Path):
    stream = LogStream(tmp_path / "test.log")
    await stream.open()
    await stream.write("line 1\n")
    await stream.write("line 2\n")
    await stream.close()
    content = (tmp_path / "test.log").read_text()
    assert content == "line 1\nline 2\n"


async def test_close_sets_is_open_false(tmp_path: Path):
    stream = LogStream(tmp_path / "test.log")
    await stream.open()
    assert stream.is_open
    await stream.close()
    assert not stream.is_open


async def test_is_open_false_before_open(tmp_path: Path):
    stream = LogStream(tmp_path / "test.log")
    assert not stream.is_open


async def test_write_without_open_raises(tmp_path: Path):
    stream = LogStream(tmp_path / "test.log")
    with pytest.raises(RuntimeError, match="Stream is not open"):
        await stream.write("data")


async def test_path_property(tmp_path: Path):
    expected = tmp_path / "output" / "my.log"
    stream = LogStream(expected)
    assert stream.path == expected


async def test_append_adds_timestamp_prefix(tmp_path: Path):
    stream = LogStream(tmp_path / "test.log")
    await stream.open()
    await stream.append("test message")
    await stream.close()
    content = (tmp_path / "test.log").read_text()
    assert content.startswith("[")
    assert "test message" in content
    assert content.endswith("]\n")


async def test_append_lines_multiple(tmp_path: Path):
    stream = LogStream(tmp_path / "test.log")
    await stream.open()
    await stream.append_lines(["line 1", "line 2", "line 3"])
    await stream.close()
    lines = (tmp_path / "test.log").read_text().strip().split("\n")
    assert len(lines) == 3
    assert "line 1" in lines[0]
    assert "line 3" in lines[2]


async def test_append_appends_to_existing(tmp_path: Path):
    file_path = tmp_path / "test.log"
    file_path.write_text("existing\n", encoding="utf-8")
    stream = LogStream(file_path)
    await stream.open()
    await stream.write("new content\n")
    await stream.close()
    content = file_path.read_text()
    assert "existing\n" in content
    assert "new content\n" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_log_stream.py -v`
Expected: Multiple FAIL — `LogStream` has no `open`/`close`/`write` methods, no `is_open`/`path` properties

- [ ] **Step 3: Rewrite LogStream with open/close lifecycle**

Replace the full content of `packages/whitebox/src/shannon_whitebox/audit/log_stream.py` with:

```python
from pathlib import Path
from typing import Any

import aiofiles

from shannon_whitebox.audit.utils import format_timestamp


class LogStream:
    """Async append-only file stream with explicit open/close lifecycle."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._file: Any = None

    async def open(self) -> None:
        """Create parent directories and open the file in append mode."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = await aiofiles.open(self.file_path, "a", encoding="utf-8")

    async def write(self, text: str) -> None:
        """Write raw text to the stream. Caller controls formatting."""
        if self._file is None:
            raise RuntimeError("Stream is not open")
        await self._file.write(text)
        await self._file.flush()

    async def close(self) -> None:
        """Flush and close the stream."""
        if self._file is not None:
            await self._file.flush()
            await self._file.close()
            self._file = None

    @property
    def is_open(self) -> bool:
        return self._file is not None

    @property
    def path(self) -> Path:
        return self.file_path

    async def append(self, line: str) -> None:
        """Backward-compatible helper: write with timestamp prefix."""
        timestamp = format_timestamp()
        await self.write(f"[{timestamp}] {line}\n")

    async def append_lines(self, lines: list[str]) -> None:
        """Write multiple lines, each with a timestamp prefix."""
        for line in lines:
            await self.append(line)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_log_stream.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/log_stream.py packages/whitebox/tests/test_log_stream.py
git commit -m "refactor(whitebox): enhance LogStream with open/close lifecycle"
```

---

### Task 4: AgentLogger

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/audit/agent_logger.py`
- Test: `packages/whitebox/tests/test_agent_logger.py`

- [ ] **Step 1: Write failing tests for AgentLogger**

Create `packages/whitebox/tests/test_agent_logger.py`:

```python
import json
from pathlib import Path

import pytest

from shannon_core.models.metrics import SessionMetadata
from shannon_whitebox.audit.agent_logger import AgentLogger


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="test-session", web_url="https://example.com", output_path=str(tmp_path))


async def test_initialize_creates_log_file(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 1)
    await logger.initialize()
    # Find the log file in agents/ directory
    agents_dir = tmp_path / "agents"
    log_files = list(agents_dir.glob("*_recon_attempt-1.log"))
    assert len(log_files) == 1
    await logger.close()


async def test_initialize_writes_header(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 1)
    await logger.initialize()
    agents_dir = tmp_path / "agents"
    log_file = list(agents_dir.glob("*_recon_attempt-1.log"))[0]
    content = log_file.read_text()
    assert "Agent: recon" in content
    assert "Attempt: 1" in content
    assert "Session: test-session" in content
    assert "Web URL: https://example.com" in content
    await logger.close()


async def test_initialize_writes_agent_start_event(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 1)
    await logger.initialize()
    agents_dir = tmp_path / "agents"
    log_file = list(agents_dir.glob("*_recon_attempt-1.log"))[0]
    lines = log_file.read_text().strip().split("\n")
    # Find the JSON line with agent_start
    json_lines = [l for l in lines if l.startswith("{")]
    assert len(json_lines) >= 1
    event = json.loads(json_lines[0])
    assert event["type"] == "agent_start"
    assert event["data"]["agentName"] == "recon"
    assert event["data"]["attemptNumber"] == 1
    assert "timestamp" in event
    await logger.close()


async def test_log_event_writes_json_line(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 1)
    await logger.initialize()
    await logger.log_event("tool_start", {"toolName": "Read", "parameters": {"file_path": "/tmp/test.py"}})
    await logger.close()

    log_file = list((tmp_path / "agents").glob("*_recon_attempt-1.log"))[0]
    lines = log_file.read_text().strip().split("\n")
    json_lines = [json.loads(l) for l in lines if l.startswith("{")]
    tool_events = [e for e in json_lines if e["type"] == "tool_start"]
    assert len(tool_events) == 1
    assert tool_events[0]["data"]["toolName"] == "Read"


async def test_log_event_includes_timestamp(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 1)
    await logger.initialize()
    await logger.log_event("agent_end", {"success": True, "duration_ms": 5000})
    await logger.close()

    log_file = list((tmp_path / "agents").glob("*_recon_attempt-1.log"))[0]
    lines = log_file.read_text().strip().split("\n")
    json_lines = [json.loads(l) for l in lines if l.startswith("{")]
    end_event = [e for e in json_lines if e["type"] == "agent_end"][0]
    assert "timestamp" in end_event
    assert end_event["timestamp"].endswith("Z")


async def test_close_prevents_further_writes(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 1)
    await logger.initialize()
    await logger.close()
    # log_event after close should not raise (it silently returns)
    await logger.log_event("tool_start", {"toolName": "Read"})


async def test_multiple_events_in_sequence(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 1)
    await logger.initialize()
    await logger.log_event("tool_start", {"toolName": "Read"})
    await logger.log_event("tool_end", {"toolName": "Read", "output": "ok"})
    await logger.log_event("agent_end", {"success": True, "duration_ms": 10000})
    await logger.close()

    log_file = list((tmp_path / "agents").glob("*_recon_attempt-1.log"))[0]
    lines = log_file.read_text().strip().split("\n")
    json_lines = [json.loads(l) for l in lines if l.startswith("{")]
    # agent_start + 3 events = 4 JSON lines
    assert len(json_lines) == 4


async def test_log_file_naming_includes_attempt(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = AgentLogger(meta, "recon", 3)
    await logger.initialize()
    await logger.close()
    log_files = list((tmp_path / "agents").glob("*_recon_attempt-3.log"))
    assert len(log_files) == 1


async def test_save_prompt_creates_markdown_file(tmp_path: Path):
    meta = _make_meta(tmp_path)
    await AgentLogger.save_prompt(meta, "recon", "You are a security analyst.")
    prompt_file = tmp_path / "prompts" / "recon.md"
    assert prompt_file.exists()
    content = prompt_file.read_text()
    assert "agent: recon" in content
    assert "session: test-session" in content
    assert "You are a security analyst." in content


async def test_save_prompt_metadata_header(tmp_path: Path):
    meta = _make_meta(tmp_path)
    await AgentLogger.save_prompt(meta, "recon", "Test prompt content")
    content = (tmp_path / "prompts" / "recon.md").read_text()
    assert content.startswith("---")
    assert "saved:" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_agent_logger.py -v`
Expected: FAIL — `shannon_whitebox.audit.agent_logger` module not found

- [ ] **Step 3: Implement AgentLogger**

Create `packages/whitebox/src/shannon_whitebox/audit/agent_logger.py`:

```python
import json
import time
from typing import Any

import aiofiles

from shannon_core.models.metrics import SessionMetadata
from shannon_whitebox.audit.log_stream import LogStream
from shannon_whitebox.audit.utils import (
    format_timestamp,
    generate_log_path,
    generate_prompt_path,
)


class AgentLogger:
    """JSON Lines agent log with a text header.

    Corresponds to audit/logger.ts in the original TypeScript project.
    Each agent run gets its own log file under the ``agents/`` directory.
    """

    def __init__(self, session_metadata: SessionMetadata, agent_name: str, attempt_number: int):
        self._meta = session_metadata
        self._agent_name = agent_name
        self._attempt = attempt_number
        self._stream: LogStream | None = None

    async def initialize(self) -> None:
        """Open the log file and write the text header + agent_start event."""
        timestamp_ms = int(time.time() * 1000)
        path = generate_log_path(self._meta, self._agent_name, timestamp_ms, self._attempt)
        self._stream = LogStream(path)
        await self._stream.open()

        header = (
            "========================================\n"
            f"Agent: {self._agent_name}\n"
            f"Attempt: {self._attempt}\n"
            f"Started: {format_timestamp()}\n"
            f"Session: {self._meta.id}\n"
            f"Web URL: {self._meta.web_url or 'N/A'}\n"
            "========================================\n\n"
        )
        await self._stream.write(header)
        await self.log_event("agent_start", {
            "agentName": self._agent_name,
            "attemptNumber": self._attempt,
        })

    async def log_event(self, event_type: str, event_data: Any) -> None:
        """Append a JSON Lines event to the agent log."""
        if self._stream is None:
            return
        event = {
            "type": event_type,
            "timestamp": format_timestamp(),
            "data": event_data,
        }
        await self._stream.write(json.dumps(event) + "\n")

    async def close(self) -> None:
        """Flush and close the underlying stream."""
        if self._stream is not None:
            await self._stream.close()
            self._stream = None

    @staticmethod
    async def save_prompt(session_metadata: SessionMetadata, agent_name: str, content: str) -> None:
        """Save a prompt snapshot as a Markdown file with YAML front-matter."""
        path = generate_prompt_path(session_metadata, agent_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "---\n"
            f"agent: {agent_name}\n"
            f"session: {session_metadata.id}\n"
            f"saved: {format_timestamp()}\n"
            "---\n\n"
        )
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(header + content)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_agent_logger.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/agent_logger.py packages/whitebox/tests/test_agent_logger.py
git commit -m "feat(whitebox): add AgentLogger with JSON Lines output and prompt snapshots"
```

---

### Task 5: WorkflowLogger

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py`
- Test: `packages/whitebox/tests/test_workflow_logger.py`

- [ ] **Step 1: Write failing tests for WorkflowLogger**

Create `packages/whitebox/tests/test_workflow_logger.py`:

```python
from pathlib import Path

from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentLogDetails, WorkflowSummary, AgentMetricsSummary, ResumeInfo
from shannon_whitebox.audit.workflow_logger import WorkflowLogger


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="test-session", web_url="https://example.com", output_path=str(tmp_path))


def _read_log(tmp_path: Path) -> str:
    return (tmp_path / "workflow.log").read_text()


async def test_initialize_creates_workflow_log(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize(workflow_id="wf-123")
    assert (tmp_path / "workflow.log").exists()
    content = _read_log(tmp_path)
    assert "Shannon Pentest - Workflow Log" in content
    assert "Workflow ID: wf-123" in content
    assert "Target URL:  https://example.com" in content
    await logger.close()


async def test_initialize_without_workflow_id(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    content = _read_log(tmp_path)
    assert "Shannon Pentest - Workflow Log" in content
    assert "Target URL:  https://example.com" in content
    assert "Workflow ID:" not in content
    await logger.close()


async def test_log_phase(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_phase("recon", "start")
    await logger.log_phase("recon", "complete")
    await logger.close()
    content = _read_log(tmp_path)
    assert "[PHASE] recon started" in content
    assert "[PHASE] recon completed" in content


async def test_log_agent_start(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_agent("recon", "start")
    await logger.close()
    content = _read_log(tmp_path)
    assert "[AGENT] recon started" in content


async def test_log_agent_end_with_details(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    details = AgentLogDetails(
        attempt_number=1,
        duration_ms=150000,
        cost_usd=0.05,
        success=True,
    )
    await logger.log_agent("recon", "end", details)
    await logger.close()
    content = _read_log(tmp_path)
    assert "[AGENT] recon ended" in content
    assert "2m 30s" in content
    assert "$0.0500" in content


async def test_log_agent_end_with_error(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    details = AgentLogDetails(success=False, error="Rate limit exceeded")
    await logger.log_agent("recon", "end", details)
    await logger.close()
    content = _read_log(tmp_path)
    assert "error: Rate limit exceeded" in content


async def test_log_tool_start(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_tool_start("recon", "Read", {"file_path": "/etc/passwd"})
    await logger.close()
    content = _read_log(tmp_path)
    assert "[TOOL] recon → Read(" in content
    assert "file_path=/etc/passwd" in content


async def test_log_tool_start_truncates_long_bash_command(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    long_cmd = "x" * 200
    await logger.log_tool_start("recon", "Bash", {"command": long_cmd})
    await logger.close()
    content = _read_log(tmp_path)
    assert "command=" in content
    assert "..." in content


async def test_log_llm_response(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_llm_response("recon", 1, "Found SQL injection vulnerability")
    await logger.close()
    content = _read_log(tmp_path)
    assert "[LLM] recon turn 1:" in content
    assert "Found SQL injection vulnerability" in content


async def test_log_llm_response_truncates_long_content(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    long_content = "A" * 300
    await logger.log_llm_response("recon", 1, long_content)
    await logger.close()
    content = _read_log(tmp_path)
    assert "..." in content


async def test_log_event(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_event("CUSTOM", "Something happened")
    await logger.close()
    content = _read_log(tmp_path)
    assert "[CUSTOM] Something happened" in content


async def test_log_error(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_error(ValueError("bad input"), context="parsing config")
    await logger.close()
    content = _read_log(tmp_path)
    assert "[ERROR]" in content
    assert "ValueError: bad input" in content
    assert "context: parsing config" in content


async def test_log_error_without_context(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_error(RuntimeError("timeout"))
    await logger.close()
    content = _read_log(tmp_path)
    assert "[ERROR]" in content
    assert "RuntimeError: timeout" in content
    assert "context:" not in content


async def test_log_workflow_complete(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize(workflow_id="wf-123")
    summary = WorkflowSummary(
        status="completed",
        total_duration_ms=330000,
        total_cost_usd=0.1234,
        completed_agents=["recon", "injection-vuln"],
        agent_metrics={
            "recon": AgentMetricsSummary(duration_ms=150000, cost_usd=0.0567),
            "injection-vuln": AgentMetricsSummary(duration_ms=180000, cost_usd=0.0234),
        },
    )
    await logger.log_workflow_complete(summary)
    await logger.close()
    content = _read_log(tmp_path)
    assert "Workflow COMPLETED" in content
    assert "Workflow ID: wf-123" in content
    assert "Status:      completed" in content
    assert "5m 30s" in content
    assert "$0.1234" in content
    assert "recon" in content
    assert "injection-vuln" in content
    assert "2m 30s" in content
    assert "3m 0s" in content


async def test_log_workflow_complete_with_error(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    summary = WorkflowSummary(
        status="failed",
        total_duration_ms=10000,
        total_cost_usd=0.01,
        completed_agents=[],
        agent_metrics={},
        error="Agent crashed unexpectedly",
    )
    await logger.log_workflow_complete(summary)
    await logger.close()
    content = _read_log(tmp_path)
    assert "failed" in content
    assert "Agent crashed unexpectedly" in content


async def test_log_resume_header(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    info = ResumeInfo(
        previous_workflow_id="wf-old",
        new_workflow_id="wf-new",
        checkpoint_hash="abc123",
        completed_agents=["recon"],
    )
    await logger.log_resume_header(info)
    await logger.close()
    content = _read_log(tmp_path)
    assert "[RESUME]" in content
    assert "wf-old" in content
    assert "wf-new" in content
    assert "abc123" in content
    assert "recon" in content


async def test_close_prevents_further_writes(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.close()
    # These should silently do nothing after close
    await logger.log_phase("test", "start")
    await logger.log_event("TEST", "message")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_workflow_logger.py -v`
Expected: FAIL — `shannon_whitebox.audit.workflow_logger` module not found

- [ ] **Step 3: Implement WorkflowLogger**

Create `packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py`:

```python
from typing import Any, Literal

from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentLogDetails, WorkflowSummary, ResumeInfo
from shannon_whitebox.audit.log_stream import LogStream
from shannon_whitebox.audit.utils import (
    format_duration,
    format_log_time,
    generate_workflow_log_path,
)


def _format_tool_params(tool_name: str, parameters: Any) -> str:
    """Per-tool smart truncation for readable workflow log lines."""
    if not isinstance(parameters, dict):
        return str(parameters)

    tool_key_map: dict[str, str] = {
        "Bash": "command",
        "Read": "file_path",
        "Write": "file_path",
        "Edit": "file_path",
        "Grep": "pattern",
        "Glob": "pattern",
    }

    key = tool_key_map.get(tool_name)
    if key and key in parameters:
        val = str(parameters[key])
        if len(val) > 80:
            val = val[:77] + "..."
        return f"{key}={val}"

    items = list(parameters.items())[:2]
    parts = [f"{k}={str(v)[:40]}" for k, v in items]
    result = ", ".join(parts)
    if len(parameters) > 2:
        result += ", ..."
    return result


class WorkflowLogger:
    """Human-readable workflow log with category-tagged lines.

    Corresponds to audit/workflow-logger.ts in the original TypeScript project.
    Writes a single workflow.log file with structured, timestamped entries.
    """

    def __init__(self, session_metadata: SessionMetadata):
        self._meta = session_metadata
        self._stream: LogStream | None = None
        self._workflow_id: str | None = None

    async def initialize(self, workflow_id: str | None = None) -> None:
        """Open the log file and write the header block."""
        self._workflow_id = workflow_id
        path = generate_workflow_log_path(self._meta)
        self._stream = LogStream(path)
        await self._stream.open()

        sep = "=" * 80
        header = f"{sep}\nShannon Pentest - Workflow Log\n{sep}\n"
        if workflow_id:
            header += f"Workflow ID: {workflow_id}\n"
        header += (
            f"Target URL:  {self._meta.web_url or 'N/A'}\n"
            f"Started:     {format_log_time()}\n"
            f"{sep}\n\n"
        )
        await self._stream.write(header)

    async def log_phase(self, phase: str, event: Literal["start", "complete"]) -> None:
        """Log a phase transition."""
        if self._stream is None:
            return
        verb = "started" if event == "start" else "completed"
        await self._stream.write(f"[{format_log_time()}] [PHASE] {phase} {verb}\n")

    async def log_agent(self, agent_name: str, event: Literal["start", "end"], details: AgentLogDetails | None = None) -> None:
        """Log an agent lifecycle event."""
        if self._stream is None:
            return
        verb = "started" if event == "start" else "ended"
        msg = f"[{format_log_time()}] [AGENT] {agent_name} {verb}"
        if details:
            parts: list[str] = []
            if details.attempt_number > 1:
                parts.append(f"attempt {details.attempt_number}")
            if details.duration_ms is not None:
                parts.append(f"duration: {format_duration(details.duration_ms)}")
            if details.cost_usd is not None:
                parts.append(f"cost: ${details.cost_usd:.4f}")
            if details.success is not None:
                parts.append("✓" if details.success else "✗")
            if details.error:
                parts.append(f"error: {details.error}")
            if parts:
                msg += " (" + ", ".join(parts) + ")"
        await self._stream.write(msg + "\n")

    async def log_tool_start(self, agent_name: str, tool_name: str, parameters: Any) -> None:
        """Log a tool invocation."""
        if self._stream is None:
            return
        formatted = _format_tool_params(tool_name, parameters)
        await self._stream.write(f"[{format_log_time()}] [TOOL] {agent_name} → {tool_name}({formatted})\n")

    async def log_llm_response(self, agent_name: str, turn: int, content: str) -> None:
        """Log an LLM response (truncated to 200 chars)."""
        if self._stream is None:
            return
        truncated = content[:200] + "..." if len(content) > 200 else content
        await self._stream.write(f"[{format_log_time()}] [LLM] {agent_name} turn {turn}: {truncated}\n")

    async def log_event(self, event_type: str, message: str) -> None:
        """Log a generic categorized event."""
        if self._stream is None:
            return
        await self._stream.write(f"[{format_log_time()}] [{event_type}] {message}\n")

    async def log_error(self, error: Exception, context: str | None = None) -> None:
        """Log an error with optional context."""
        if self._stream is None:
            return
        msg = f"[{format_log_time()}] [ERROR] {type(error).__name__}: {error}"
        if context:
            msg += f" (context: {context})"
        await self._stream.write(msg + "\n")

    async def log_workflow_complete(self, summary: WorkflowSummary) -> None:
        """Write the final summary block (single write)."""
        if self._stream is None:
            return
        sep = "=" * 80
        dash = "─" * 40
        lines = [
            f"\n{sep}\n",
            "Workflow COMPLETED\n",
            f"{dash}\n",
            f"Workflow ID: {self._workflow_id or 'N/A'}\n",
            f"Status:      {summary.status}\n",
            f"Duration:    {format_duration(summary.total_duration_ms)}\n",
            f"Total Cost:  ${summary.total_cost_usd:.4f}\n",
            f"Agents:      {len(summary.completed_agents)} completed\n",
            "\n",
            "Agent Breakdown:\n",
        ]
        for name in summary.completed_agents:
            metrics = summary.agent_metrics.get(name)
            if metrics:
                cost_str = f", ${metrics.cost_usd:.4f}" if metrics.cost_usd is not None else ""
                lines.append(f"  - {name} ({format_duration(metrics.duration_ms)}{cost_str})\n")
            else:
                lines.append(f"  - {name}\n")
        if summary.error:
            lines.append(f"\nError: {summary.error}\n")
        lines.append(f"{sep}\n")
        await self._stream.write("".join(lines))

    async def log_resume_header(self, resume_info: ResumeInfo) -> None:
        """Write a resume header block."""
        if self._stream is None:
            return
        header = (
            f"\n[{format_log_time()}] [RESUME] Resuming workflow\n"
            f"  Previous Workflow ID: {resume_info.previous_workflow_id}\n"
            f"  New Workflow ID:      {resume_info.new_workflow_id}\n"
            f"  Checkpoint:           {resume_info.checkpoint_hash}\n"
            f"  Completed Agents:     {', '.join(resume_info.completed_agents)}\n\n"
        )
        await self._stream.write(header)

    async def close(self) -> None:
        """Flush and close the underlying stream."""
        if self._stream is not None:
            await self._stream.close()
            self._stream = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_workflow_logger.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py packages/whitebox/tests/test_workflow_logger.py
git commit -m "feat(whitebox): add WorkflowLogger with categorized human-readable output"
```

---

### Task 6: MetricsTracker

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/audit/metrics_tracker.py`
- Test: `packages/whitebox/tests/test_metrics_tracker.py`

- [ ] **Step 1: Write failing tests for MetricsTracker**

Create `packages/whitebox/tests/test_metrics_tracker.py`:

```python
import json
from pathlib import Path

from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentEndResult
from shannon_whitebox.audit.metrics_tracker import MetricsTracker


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="test-session", web_url="https://example.com", output_path=str(tmp_path))


def _read_session_json(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "session.json").read_text())


async def test_initialize_creates_session_json(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize(workflow_id="wf-123")
    data = _read_session_json(tmp_path)
    assert data["session"]["id"] == "test-session"
    assert data["session"]["status"] == "in-progress"
    assert data["session"]["originalWorkflowId"] == "wf-123"
    assert data["session"]["webUrl"] == "https://example.com"
    assert "createdAt" in data["session"]
    assert data["session"]["resumeAttempts"] == []
    assert "metrics" in data


async def test_initialize_without_workflow_id(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    data = _read_session_json(tmp_path)
    assert data["session"]["originalWorkflowId"] is None


async def test_start_agent_records_agent(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    metrics = tracker.get_metrics()
    assert "recon" in metrics["agents"]
    assert metrics["agents"]["recon"]["attempts"] == 1


async def test_end_agent_updates_metrics(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    result = AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05, model="claude-sonnet-4-6")
    await tracker.end_agent("recon", result)
    data = _read_session_json(tmp_path)
    assert data["metrics"]["agents"]["recon"]["duration_ms"] == 5000
    assert data["metrics"]["agents"]["recon"]["cost_usd"] == 0.05
    assert data["metrics"]["agents"]["recon"]["success"] is True
    assert data["metrics"]["agents"]["recon"]["model"] == "claude-sonnet-4-6"
    assert data["metrics"]["total_duration_ms"] == 5000
    assert data["metrics"]["total_cost_usd"] == 0.05


async def test_end_agent_accumulates_totals(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05))
    tracker.start_agent("injection", 1)
    await tracker.end_agent("injection", AgentEndResult(success=True, duration_ms=3000, cost_usd=0.03))
    data = _read_session_json(tmp_path)
    assert data["metrics"]["total_duration_ms"] == 8000
    assert data["metrics"]["total_cost_usd"] == 0.08


async def test_end_agent_with_error(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    result = AgentEndResult(success=False, duration_ms=1000, cost_usd=0.01, error="Rate limited")
    await tracker.end_agent("recon", result)
    data = _read_session_json(tmp_path)
    assert data["metrics"]["agents"]["recon"]["error"] == "Rate limited"


async def test_update_session_status(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    await tracker.update_session_status("completed")
    data = _read_session_json(tmp_path)
    assert data["session"]["status"] == "completed"


async def test_add_resume_attempt(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    await tracker.add_resume_attempt("wf-new", ["recon"], checkpoint="hash123")
    data = _read_session_json(tmp_path)
    assert len(data["session"]["resumeAttempts"]) == 1
    attempt = data["session"]["resumeAttempts"][0]
    assert attempt["workflowId"] == "wf-new"
    assert attempt["terminatedAgents"] == ["recon"]
    assert attempt["checkpoint"] == "hash123"


async def test_add_resume_attempt_without_checkpoint(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    await tracker.add_resume_attempt("wf-new", ["recon"])
    data = _read_session_json(tmp_path)
    assert data["session"]["resumeAttempts"][0]["checkpoint"] is None


async def test_multiple_resume_attempts(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    await tracker.add_resume_attempt("wf-2", ["recon"])
    await tracker.add_resume_attempt("wf-3", ["recon", "injection"])
    data = _read_session_json(tmp_path)
    assert len(data["session"]["resumeAttempts"]) == 2


async def test_reload_reads_from_disk(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    # Simulate external modification
    data = _read_session_json(tmp_path)
    data["session"]["status"] = "externally-modified"
    (tmp_path / "session.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    await tracker.reload()
    # get_metrics is from in-memory state; verify through internal _data
    assert tracker._data["session"]["status"] == "externally-modified"


async def test_get_metrics_returns_dict(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05))
    metrics = tracker.get_metrics()
    assert "agents" in metrics
    assert "total_duration_ms" in metrics
    assert metrics["total_duration_ms"] == 5000


async def test_atomic_write_uses_temp_file(tmp_path: Path):
    """Verify no stale .tmp files remain after write."""
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    # After initialize, no .tmp file should remain
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_metrics_tracker.py -v`
Expected: FAIL — `shannon_whitebox.audit.metrics_tracker` module not found

- [ ] **Step 3: Implement MetricsTracker**

Create `packages/whitebox/src/shannon_whitebox/audit/metrics_tracker.py`:

```python
import json
import os
from pathlib import Path

from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentEndResult
from shannon_whitebox.audit.utils import (
    format_timestamp,
    generate_session_json_path,
)


class MetricsTracker:
    """Manages session.json with atomic read/write.

    Corresponds to audit/metrics-tracker.ts in the original TypeScript project.
    Uses temp-file + os.replace for crash-safe writes.
    """

    def __init__(self, session_metadata: SessionMetadata):
        self._meta = session_metadata
        self._path = generate_session_json_path(session_metadata)
        self._data: dict = {}

    async def initialize(self, workflow_id: str | None = None) -> None:
        """Create the initial session.json structure."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        ts = format_timestamp()
        self._data = {
            "session": {
                "id": self._meta.id,
                "webUrl": self._meta.web_url,
                "status": "in-progress",
                "createdAt": ts,
                "originalWorkflowId": workflow_id,
                "resumeAttempts": [],
            },
            "metrics": {
                "total_duration_ms": 0,
                "total_cost_usd": 0,
                "phases": {},
                "agents": {},
            },
        }
        await self._atomic_write()

    def start_agent(self, agent_name: str, attempt_number: int) -> None:
        """Record that an agent has started (in-memory only)."""
        if agent_name not in self._data["metrics"]["agents"]:
            self._data["metrics"]["agents"][agent_name] = {
                "duration_ms": 0,
                "cost_usd": 0,
                "attempts": attempt_number,
            }

    async def end_agent(self, agent_name: str, result: AgentEndResult) -> None:
        """Persist agent results and update running totals."""
        agents = self._data["metrics"]["agents"]
        if agent_name not in agents:
            agents[agent_name] = {}
        agents[agent_name].update({
            "duration_ms": result.duration_ms,
            "cost_usd": result.cost_usd,
            "success": result.success,
            "attempt_number": result.attempt_number,
            "model": result.model,
        })
        if result.error:
            agents[agent_name]["error"] = result.error

        self._data["metrics"]["total_duration_ms"] += result.duration_ms
        self._data["metrics"]["total_cost_usd"] += result.cost_usd

        await self._atomic_write()

    async def update_session_status(self, status: str) -> None:
        """Update the session status field."""
        self._data["session"]["status"] = status
        await self._atomic_write()

    async def add_resume_attempt(self, workflow_id: str, terminated: list[str], checkpoint: str | None = None) -> None:
        """Append a resume attempt to the session record."""
        attempt = {
            "workflowId": workflow_id,
            "terminatedAgents": terminated,
            "checkpoint": checkpoint,
        }
        self._data["session"]["resumeAttempts"].append(attempt)
        await self._atomic_write()

    async def reload(self) -> None:
        """Reload session.json from disk (pick up external changes)."""
        if self._path.exists():
            content = self._path.read_text(encoding="utf-8")
            self._data = json.loads(content)

    def get_metrics(self) -> dict:
        """Return the current metrics dict."""
        return self._data.get("metrics", {})

    async def _atomic_write(self) -> None:
        """Write to a temp file then atomically replace session.json."""
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")
        os.replace(str(tmp), str(self._path))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_metrics_tracker.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/metrics_tracker.py packages/whitebox/tests/test_metrics_tracker.py
git commit -m "feat(whitebox): add MetricsTracker with atomic session.json writes"
```

---

### Task 7: AuditSession Facade

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/audit/session.py`
- Modify: `packages/whitebox/src/shannon_whitebox/audit/__init__.py`
- Test: `packages/whitebox/tests/test_audit_session.py`

- [ ] **Step 1: Write failing tests for the new AuditSession**

Create `packages/whitebox/tests/test_audit_session.py`:

```python
import json
from pathlib import Path

from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentEndResult, WorkflowSummary, AgentMetricsSummary, ResumeInfo
from shannon_whitebox.audit.session import AuditSession


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="test-session", web_url="https://example.com", output_path=str(tmp_path))


async def test_initialize_creates_directories(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize(workflow_id="wf-1")
    assert (tmp_path / "agents").is_dir()
    assert (tmp_path / "prompts").is_dir()
    assert (tmp_path / "deliverables").is_dir()
    assert (tmp_path / "workflow.log").exists()
    assert (tmp_path / "session.json").exists()


async def test_start_agent_creates_agent_log(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "Analyze the target", attempt=1)
    # Agent log file should exist
    log_files = list((tmp_path / "agents").glob("*_recon_attempt-1.log"))
    assert len(log_files) == 1


async def test_start_agent_saves_prompt(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "Analyze the target", attempt=1)
    assert (tmp_path / "prompts" / "recon.md").exists()


async def test_log_event_dispatches_to_both_loggers(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "prompt", attempt=1)
    await session.log_event("tool_start", {"toolName": "Read", "parameters": {"file_path": "/tmp/test"}})
    # Check agent log has JSON event
    agent_log = list((tmp_path / "agents").glob("*.log"))[0]
    agent_content = agent_log.read_text()
    json_lines = [l for l in agent_content.split("\n") if l.startswith("{")]
    tool_events = [json.loads(l) for l in json_lines if '"tool_start"' in l]
    assert len(tool_events) == 1
    # Check workflow log has human-readable event
    wf_content = (tmp_path / "workflow.log").read_text()
    assert "[TOOL] recon → Read(" in wf_content


async def test_log_event_dispatches_llm_response(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "prompt", attempt=1)
    await session.log_event("llm_response", {"turn": 1, "content": "Found XSS vulnerability"})
    wf_content = (tmp_path / "workflow.log").read_text()
    assert "[LLM] recon turn 1:" in wf_content
    assert "Found XSS vulnerability" in wf_content


async def test_end_agent_updates_metrics(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "prompt", attempt=1)
    result = AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05, model="claude-sonnet-4-6")
    await session.end_agent("recon", result)
    data = json.loads((tmp_path / "session.json").read_text())
    assert data["metrics"]["agents"]["recon"]["success"] is True
    assert data["metrics"]["total_cost_usd"] == 0.05


async def test_end_agent_writes_agent_end_event(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "prompt", attempt=1)
    result = AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05)
    await session.end_agent("recon", result)
    agent_log = list((tmp_path / "agents").glob("*.log"))[0]
    content = agent_log.read_text()
    json_lines = [json.loads(l) for l in content.split("\n") if l.startswith("{")]
    end_events = [e for e in json_lines if e["type"] == "agent_end"]
    assert len(end_events) == 1
    assert end_events[0]["data"]["success"] is True


async def test_log_phase_start_and_complete(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.log_phase_start("recon")
    await session.log_phase_complete("recon")
    wf_content = (tmp_path / "workflow.log").read_text()
    assert "[PHASE] recon started" in wf_content
    assert "[PHASE] recon completed" in wf_content


async def test_log_workflow_complete(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize(workflow_id="wf-1")
    summary = WorkflowSummary(
        status="completed",
        total_duration_ms=300000,
        total_cost_usd=0.12,
        completed_agents=["recon"],
        agent_metrics={"recon": AgentMetricsSummary(duration_ms=300000, cost_usd=0.12)},
    )
    await session.log_workflow_complete(summary)
    wf_content = (tmp_path / "workflow.log").read_text()
    assert "Workflow COMPLETED" in wf_content
    # Check session status updated
    data = json.loads((tmp_path / "session.json").read_text())
    assert data["session"]["status"] == "completed"


async def test_update_session_status(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.update_session_status("paused")
    data = json.loads((tmp_path / "session.json").read_text())
    assert data["session"]["status"] == "paused"


async def test_add_resume_attempt(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.add_resume_attempt("wf-2", ["recon"], checkpoint="hash123")
    data = json.loads((tmp_path / "session.json").read_text())
    assert len(data["session"]["resumeAttempts"]) == 1
    assert data["session"]["resumeAttempts"][0]["workflowId"] == "wf-2"


async def test_log_resume_header(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    info = ResumeInfo(
        previous_workflow_id="wf-old",
        new_workflow_id="wf-new",
        checkpoint_hash="abc123",
        completed_agents=["recon"],
    )
    await session.log_resume_header(info)
    wf_content = (tmp_path / "workflow.log").read_text()
    assert "[RESUME]" in wf_content
    assert "wf-old" in wf_content


async def test_get_metrics(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "prompt", attempt=1)
    await session.end_agent("recon", AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05))
    metrics = await session.get_metrics()
    assert metrics["total_duration_ms"] == 5000
    assert metrics["total_cost_usd"] == 0.05


async def test_full_lifecycle(tmp_path: Path):
    """End-to-end: initialize → start_agent → log_events → end_agent → complete."""
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize(workflow_id="wf-lifecycle")

    await session.log_phase_start("recon")
    await session.start_agent("recon", "Analyze the target application", attempt=1)
    await session.log_event("tool_start", {"toolName": "Read", "parameters": {"file_path": "/app/main.py"}})
    await session.log_event("llm_response", {"turn": 1, "content": "Identified SQL injection points"})
    await session.end_agent("recon", AgentEndResult(success=True, duration_ms=15000, cost_usd=0.08))
    await session.log_phase_complete("recon")

    summary = WorkflowSummary(
        status="completed",
        total_duration_ms=15000,
        total_cost_usd=0.08,
        completed_agents=["recon"],
        agent_metrics={"recon": AgentMetricsSummary(duration_ms=15000, cost_usd=0.08)},
    )
    await session.log_workflow_complete(summary)

    # Verify workflow log
    wf = (tmp_path / "workflow.log").read_text()
    assert "Shannon Pentest - Workflow Log" in wf
    assert "Workflow ID: wf-lifecycle" in wf
    assert "[PHASE] recon started" in wf
    assert "[AGENT] recon started" in wf
    assert "[TOOL] recon → Read(" in wf
    assert "[LLM] recon turn 1:" in wf
    assert "[AGENT] recon ended" in wf
    assert "[PHASE] recon completed" in wf
    assert "Workflow COMPLETED" in wf

    # Verify session.json
    data = json.loads((tmp_path / "session.json").read_text())
    assert data["session"]["status"] == "completed"
    assert data["metrics"]["total_duration_ms"] == 15000
    assert data["metrics"]["agents"]["recon"]["success"] is True

    # Verify agent log
    agent_log = list((tmp_path / "agents").glob("*.log"))[0]
    agent_content = agent_log.read_text()
    assert "Agent: recon" in agent_content
    json_lines = [json.loads(l) for l in agent_content.split("\n") if l.startswith("{")]
    assert len(json_lines) == 3  # agent_start + tool_start + agent_end
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_audit_session.py -v`
Expected: FAIL — `AuditSession` doesn't accept `SessionMetadata` or have expected methods

- [ ] **Step 3: Rewrite AuditSession as facade**

Replace the full content of `packages/whitebox/src/shannon_whitebox/audit/session.py` with:

```python
import asyncio
from typing import Any

from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentEndResult, AgentLogDetails, ResumeInfo, WorkflowSummary

from .agent_logger import AgentLogger
from .metrics_tracker import MetricsTracker
from .utils import initialize_audit_structure
from .workflow_logger import WorkflowLogger


class AuditSession:
    """Facade coordinating AgentLogger, WorkflowLogger, and MetricsTracker.

    This replaces the previous flat implementation. Callers interact only
    with this class; it dispatches to the appropriate component internally.
    """

    def __init__(self, session_metadata: SessionMetadata):
        self._meta = session_metadata
        self._agent_logger: AgentLogger | None = None
        self._workflow_logger: WorkflowLogger | None = None
        self._metrics_tracker: MetricsTracker | None = None
        self._lock = asyncio.Lock()
        self._current_agent_name: str | None = None

    async def initialize(self, workflow_id: str | None = None) -> None:
        """Create directory structure and initialize all components."""
        initialize_audit_structure(self._meta)
        self._workflow_logger = WorkflowLogger(self._meta)
        await self._workflow_logger.initialize(workflow_id)
        self._metrics_tracker = MetricsTracker(self._meta)
        await self._metrics_tracker.initialize(workflow_id)

    async def start_agent(self, agent_name: str, prompt: str, attempt: int = 1) -> None:
        """Initialize an agent logger, save prompt, and log start events."""
        self._current_agent_name = agent_name
        self._agent_logger = AgentLogger(self._meta, agent_name, attempt)
        await self._agent_logger.initialize()
        await AgentLogger.save_prompt(self._meta, agent_name, prompt)

        if self._workflow_logger:
            await self._workflow_logger.log_agent(
                agent_name, "start", AgentLogDetails(attempt_number=attempt),
            )
        if self._metrics_tracker:
            self._metrics_tracker.start_agent(agent_name, attempt)

    async def log_event(self, event_type: str, event_data: Any) -> None:
        """Dispatch events to both agent log (JSON) and workflow log (human-readable)."""
        if self._agent_logger:
            await self._agent_logger.log_event(event_type, event_data)
        if self._workflow_logger:
            if event_type == "tool_start" and isinstance(event_data, dict):
                await self._workflow_logger.log_tool_start(
                    self._current_agent_name or "unknown",
                    event_data.get("toolName", "unknown"),
                    event_data.get("parameters", {}),
                )
            elif event_type == "llm_response" and isinstance(event_data, dict):
                await self._workflow_logger.log_llm_response(
                    self._current_agent_name or "unknown",
                    event_data.get("turn", 0),
                    event_data.get("content", ""),
                )

    async def end_agent(self, agent_name: str, result: AgentEndResult) -> None:
        """Close agent log, update metrics, and log end events."""
        if self._agent_logger:
            await self._agent_logger.log_event("agent_end", {
                "success": result.success,
                "duration_ms": result.duration_ms,
            })
            await self._agent_logger.close()
            self._agent_logger = None

        if self._workflow_logger:
            details = AgentLogDetails(
                attempt_number=result.attempt_number,
                duration_ms=result.duration_ms,
                cost_usd=result.cost_usd,
                success=result.success,
                error=result.error,
            )
            await self._workflow_logger.log_agent(agent_name, "end", details)

        if self._metrics_tracker:
            async with self._lock:
                await self._metrics_tracker.reload()
                await self._metrics_tracker.end_agent(agent_name, result)

        self._current_agent_name = None

    async def log_phase_start(self, phase: str) -> None:
        """Log a phase start event."""
        if self._workflow_logger:
            await self._workflow_logger.log_phase(phase, "start")

    async def log_phase_complete(self, phase: str) -> None:
        """Log a phase complete event."""
        if self._workflow_logger:
            await self._workflow_logger.log_phase(phase, "complete")

    async def log_workflow_complete(self, summary: WorkflowSummary) -> None:
        """Write the workflow summary and update session status."""
        if self._workflow_logger:
            await self._workflow_logger.log_workflow_complete(summary)
            await self._workflow_logger.close()
        if self._metrics_tracker:
            await self._metrics_tracker.update_session_status(summary.status)

    async def update_session_status(self, status: str) -> None:
        """Update the session status in session.json."""
        if self._metrics_tracker:
            await self._metrics_tracker.update_session_status(status)

    async def add_resume_attempt(self, workflow_id: str, terminated: list[str], checkpoint: str | None = None) -> None:
        """Record a resume attempt with lock-protected metrics update."""
        if self._metrics_tracker:
            async with self._lock:
                await self._metrics_tracker.reload()
                await self._metrics_tracker.add_resume_attempt(workflow_id, terminated, checkpoint)

    async def log_resume_header(self, resume_info: ResumeInfo) -> None:
        """Write a resume header to the workflow log."""
        if self._workflow_logger:
            await self._workflow_logger.log_resume_header(resume_info)

    async def get_metrics(self) -> dict:
        """Return the current metrics dict."""
        if self._metrics_tracker:
            return self._metrics_tracker.get_metrics()
        return {}
```

- [ ] **Step 4: Update `__init__.py` to export AuditSession**

Replace the content of `packages/whitebox/src/shannon_whitebox/audit/__init__.py` with:

```python
from .session import AuditSession

__all__ = ["AuditSession"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_audit_session.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/session.py packages/whitebox/src/shannon_whitebox/audit/__init__.py packages/whitebox/tests/test_audit_session.py
git commit -m "refactor(whitebox): rewrite AuditSession as facade coordinating three components"
```

---

### Task 8: Full Suite Regression

**Files:** None new — run all tests to verify no regressions.

- [ ] **Step 1: Run the complete test suite**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/ packages/whitebox/tests/ -v`
Expected: All PASS — no regressions in core or whitebox tests

- [ ] **Step 2: Fix any failures if needed**

If any test fails due to the `SessionMetadata.web_url` change or import path changes, fix them and re-run. The key change that could affect existing tests is `web_url` becoming `Optional` — all existing tests that create `SessionMetadata` already provide `web_url`, so no breakage expected.

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec Section | Task |
|---|---|
| `utils.py` — format_duration, format_timestamp, format_log_time, sanitize_hostname | Task 2 |
| `utils.py` — generate_audit_path, generate_log_path, generate_prompt_path, generate_workflow_log_path, generate_session_json_path | Task 2 |
| `utils.py` — initialize_audit_structure | Task 2 |
| `log_stream.py` — open/close lifecycle, write raw, is_open, path, backward-compat append | Task 3 |
| `agent_logger.py` — AgentLogger with header, JSON Lines, close, save_prompt | Task 4 |
| `workflow_logger.py` — WorkflowLogger with header, log_phase, log_agent, log_tool_start, log_llm_response, log_event, log_error, log_workflow_complete, log_resume_header | Task 5 |
| `metrics_tracker.py` — MetricsTracker with initialize, start_agent, end_agent, update_session_status, add_resume_attempt, reload, get_metrics, atomic write | Task 6 |
| `session.py` — AuditSession facade with all methods | Task 7 |
| Data types — AgentEndResult, AgentLogDetails, AgentMetricsSummary, WorkflowSummary, ResumeInfo | Task 1 |
| SessionMetadata.web_url optional | Task 1 |

### 2. Placeholder Scan

No TBD, TODO, "implement later", "add appropriate error handling", or placeholder patterns found.

### 3. Type Consistency

- `AgentEndResult` defined in Task 1, used in Tasks 6, 7 — fields match
- `AgentLogDetails` defined in Task 1, used in Tasks 5, 7 — fields match
- `WorkflowSummary` defined in Task 1, used in Tasks 5, 7 — fields match
- `ResumeInfo` defined in Task 1, used in Tasks 5, 7 — fields match
- `AgentMetricsSummary` defined in Task 1, used in Tasks 5, 7 — fields match
- `SessionMetadata` from `shannon_core.models.metrics` — used consistently everywhere
- `LogStream` methods: `open()`, `write()`, `close()`, `is_open`, `path` — consistent across Tasks 3–7
- `AgentLogger` methods: `initialize()`, `log_event()`, `close()`, `save_prompt()` — consistent across Tasks 4, 7
- `WorkflowLogger` methods — consistent across Tasks 5, 7
- `MetricsTracker` methods — consistent across Tasks 6, 7
