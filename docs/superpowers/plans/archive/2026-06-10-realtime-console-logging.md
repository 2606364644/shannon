# Real-time Console Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace repetitive progress polling with real-time event-stream console output that shows [AGENT], [TOOL], [LLM] events during white-box scans.

**Architecture:** Wire WorkflowLogger into agent activities so events are written to `workflow.log`. Replace the Temporal polling loop in `run_scan()` with a `watchdog`-based file watcher that tails `workflow.log` to stdout in real-time.

**Tech Stack:** Python 3, asyncio, aiofiles (already in project), watchdog (already in project), Temporal (existing).

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py` | Add header-skipping logic (only write header if file is empty) |
| Modify | `packages/whitebox/src/shannon_whitebox/audit/log_stream.py` | No change needed — append mode already handles multi-writer |
| Create | `packages/core/src/shannon_core/agents/workflow_event_bridge.py` | ToolAuditLogger that also writes to WorkflowLogger |
| Create | `packages/whitebox/src/shannon_whitebox/audit/activity_context.py` | Async context manager: creates WorkflowLogger + bridge, logs agent lifecycle |
| Modify | `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:74-99` | Wrap `run_agent` with ActivityAuditContext |
| Create | `packages/whitebox/src/shannon_whitebox/pipeline/phase_logger.py` | Lightweight activity that appends [PHASE] lines to workflow.log |
| Modify | `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:111-204` | Call phase_logger before each agent execution |
| Modify | `packages/whitebox/src/shannon_whitebox/worker.py` | Remove `poll_workflow_progress`, add `tail_log` + `_AsyncLogHandler` |
| Modify | `packages/core/src/shannon_core/agents/message_dispatcher.py:29-34` | Add `llm_callback` parameter |
| Modify | `packages/core/src/shannon_core/agents/runner.py:134-136` | Pass `llm_callback` through to MessageDispatcher |
| Modify | `packages/core/src/shannon_core/agents/executor.py:25-37` | Accept and wire `llm_callback` from bridge |
| Test | `packages/whitebox/tests/test_workflow_logger.py` | Add tests for header-skipping |
| Test | `packages/core/tests/test_workflow_event_bridge.py` | New: bridge tests |
| Test | `packages/whitebox/tests/test_activity_context.py` | New: context manager tests |
| Test | `packages/whitebox/tests/test_worker.py` | Update: verify polling removed, file watcher works |
| Test | `packages/whitebox/tests/test_worker_progress.py` | Remove: polling function no longer exists |

---

### Task 1: WorkflowLogger Header Skipping

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py:50-66`
- Test: `packages/whitebox/tests/test_workflow_logger.py`

When `initialize()` is called on an already-existing `workflow.log`, it must not write a duplicate header. Extract the header into `_write_header()` and only call it when the file is empty.

- [ ] **Step 1: Write the failing test**

Add to `packages/whitebox/tests/test_workflow_logger.py`:

```python
async def test_initialize_skips_header_on_existing_file(tmp_path: Path):
    meta = _make_meta(tmp_path)
    audit_dir = _audit_dir(tmp_path)
    audit_dir.mkdir(parents=True, exist_ok=True)
    log_file = audit_dir / "workflow.log"
    log_file.write_text("[existing content]\n", encoding="utf-8")

    logger = WorkflowLogger(meta)
    await logger.initialize(workflow_id="wf-456")
    content = _read_log(tmp_path)
    assert content.startswith("[existing content]\n")
    assert "Shannon Pentest" not in content
    await logger.close()


async def test_initialize_writes_header_on_empty_file(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize(workflow_id="wf-789")
    content = _read_log(tmp_path)
    assert "Shannon Pentest - Workflow Log" in content
    assert "Workflow ID: wf-789" in content
    await logger.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/whitebox/tests/test_workflow_logger.py::test_initialize_skips_header_on_existing_file packages/whitebox/tests/test_workflow_logger.py::test_initialize_writes_header_on_empty_file -v`
Expected: `test_initialize_skips_header_on_existing_file` FAILS (header is always written).

- [ ] **Step 3: Refactor `WorkflowLogger.initialize()` to skip header when file has content**

Replace `packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py` lines 50–66 with:

```python
    async def initialize(self, workflow_id: str | None = None) -> None:
        """Open the log file. Write header only if file is new (empty)."""
        self._workflow_id = workflow_id
        path = generate_workflow_log_path(self._meta)
        self._stream = LogStream(path)
        await self._stream.open()
        if path.stat().st_size == 0:
            await self._write_header(workflow_id)

    async def _write_header(self, workflow_id: str | None) -> None:
        """Write the header block to the log file."""
        if self._stream is None:
            return
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
```

- [ ] **Step 4: Run all WorkflowLogger tests to verify nothing is broken**

Run: `uv run pytest packages/whitebox/tests/test_workflow_logger.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py packages/whitebox/tests/test_workflow_logger.py
git commit -m "refactor(workflow-logger): skip header when file already has content"
```

---

### Task 2: WorkflowEventBridge

**Files:**
- Create: `packages/core/src/shannon_core/agents/workflow_event_bridge.py`
- Test: `packages/core/tests/test_workflow_event_bridge.py`

A `ToolAuditLogger` that delegates to both the existing `ActivityToolAuditLogger` (for Temporal logging) and `WorkflowLogger` (for file logging).

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_workflow_event_bridge.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shannon_core.agents.workflow_event_bridge import WorkflowEventBridge


@pytest.fixture
def mock_activity_logger():
    return MagicMock()


@pytest.fixture
def mock_workflow_logger():
    wl = AsyncMock()
    wl.log_tool_start = AsyncMock()
    wl.log_tool_end = AsyncMock()
    wl.log_error = AsyncMock()
    return wl


async def test_log_tool_start_writes_to_both(
    mock_activity_logger, mock_workflow_logger
):
    bridge = WorkflowEventBridge(mock_activity_logger, mock_workflow_logger, "recon")
    await bridge.log_tool_start("Bash", {"command": "ls -la"})

    # Should call ActivityLogger.info (via parent class)
    mock_activity_logger.info.assert_called_once()
    # Should also call WorkflowLogger.log_tool_start
    mock_workflow_logger.log_tool_start.assert_awaited_once_with(
        "recon", "Bash", {"command": "ls -la"}
    )


async def test_log_tool_end_writes_to_activity_logger_only(
    mock_activity_logger, mock_workflow_logger
):
    bridge = WorkflowEventBridge(mock_activity_logger, mock_workflow_logger, "recon")
    await bridge.log_tool_end("output data")

    mock_activity_logger.info.assert_called_once()
    mock_workflow_logger.log_tool_end.assert_not_awaited()


async def test_log_error_writes_to_both(
    mock_activity_logger, mock_workflow_logger
):
    bridge = WorkflowEventBridge(mock_activity_logger, mock_workflow_logger, "recon")
    await bridge.log_error("something broke", turn_count=3, duration_ms=5000)

    mock_activity_logger.error.assert_called_once()
    mock_workflow_logger.log_error.assert_awaited_once()
    call_args = mock_workflow_logger.log_error.call_args
    assert isinstance(call_args[0][0], Exception)
    assert "something broke" in str(call_args[0][0])
    assert call_args[1].get("context") == "turn 3"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_workflow_event_bridge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.agents.workflow_event_bridge'`

- [ ] **Step 3: Implement WorkflowEventBridge**

Create `packages/core/src/shannon_core/agents/workflow_event_bridge.py`:

```python
"""Bridge that sends tool audit events to both ActivityLogger and WorkflowLogger."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .tool_audit_logger import ActivityToolAuditLogger

if TYPE_CHECKING:
    from shannon_core.logging.activity_logger import ActivityLogger
    from shannon_whitebox.audit.workflow_logger import WorkflowLogger


class WorkflowEventBridge(ActivityToolAuditLogger):
    """Delegates tool audit events to ActivityLogger (Temporal) AND WorkflowLogger (file).

    Preserves existing Temporal activity logging while also writing human-readable
    events to workflow.log for real-time console display.
    """

    def __init__(
        self,
        activity_logger: ActivityLogger,
        workflow_logger: WorkflowLogger,
        agent_name: str,
    ) -> None:
        super().__init__(activity_logger)
        self._wf = workflow_logger
        self._agent = agent_name

    @property
    def workflow_logger(self) -> WorkflowLogger:
        """Expose the WorkflowLogger for LLM callback wiring."""
        return self._wf

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        await super().log_tool_start(tool_name, parameters)
        await self._wf.log_tool_start(self._agent, tool_name, parameters)

    async def log_tool_end(self, result: Any) -> None:
        await super().log_tool_end(result)

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        await super().log_error(error, turn_count=turn_count, duration_ms=duration_ms)
        await self._wf.log_error(Exception(error), context=f"turn {turn_count}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_workflow_event_bridge.py -v`
Expected: All 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/workflow_event_bridge.py packages/core/tests/test_workflow_event_bridge.py
git commit -m "feat(core): add WorkflowEventBridge — dual-sink tool audit logger"
```

---

### Task 3: ActivityAuditContext

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/audit/activity_context.py`
- Test: `packages/whitebox/tests/test_activity_context.py`

Async context manager that creates a WorkflowLogger per agent activity, provides a WorkflowEventBridge for the executor, and logs agent start/end events.

- [ ] **Step 1: Write the failing test**

Create `packages/whitebox/tests/test_activity_context.py`:

```python
import pytest
from pathlib import Path

from shannon_whitebox.audit.activity_context import ActivityAuditContext


def _workspace_dir(tmp_path: Path) -> str:
    ws = tmp_path / "workspaces" / "test-ws"
    ws.mkdir(parents=True, exist_ok=True)
    return str(ws)


async def test_context_creates_workflow_log_and_bridge(tmp_path: Path):
    ws = _workspace_dir(tmp_path)
    async with ActivityAuditContext("recon", ws, "https://example.com") as ctx:
        assert ctx.bridge is not None
        log_path = Path(ws) / "workflow.log"
        assert log_path.exists()
        content = log_path.read_text()
        assert "Shannon Pentest - Workflow Log" in content
        assert "[AGENT] recon started" in content


async def test_context_skips_header_on_second_agent(tmp_path: Path):
    ws = _workspace_dir(tmp_path)
    async with ActivityAuditContext("pre-recon", ws, "https://example.com"):
        pass
    async with ActivityAuditContext("recon", ws, "https://example.com"):
        pass
    log_path = Path(ws) / "workflow.log"
    content = log_path.read_text()
    # Header should appear only once
    assert content.count("Shannon Pentest - Workflow Log") == 1
    assert "[AGENT] pre-recon started" in content
    assert "[AGENT] recon started" in content


async def test_log_agent_end_writes_end_event(tmp_path: Path):
    ws = _workspace_dir(tmp_path)
    async with ActivityAuditContext("recon", ws) as ctx:
        await ctx.log_agent_end(duration_ms=90000, cost_usd=0.05, success=True)
    log_path = Path(ws) / "workflow.log"
    content = log_path.read_text()
    assert "[AGENT] recon ended" in content
    assert "1m 30s" in content
    assert "$0.0500" in content


async def test_log_agent_end_with_error(tmp_path: Path):
    ws = _workspace_dir(tmp_path)
    async with ActivityAuditContext("recon", ws) as ctx:
        await ctx.log_agent_end(
            duration_ms=5000, success=False, error="Rate limit exceeded"
        )
    log_path = Path(ws) / "workflow.log"
    content = log_path.read_text()
    assert "[AGENT] recon ended" in content
    assert "error: Rate limit exceeded" in content


async def test_context_closes_on_exception(tmp_path: Path):
    ws = _workspace_dir(tmp_path)
    try:
        async with ActivityAuditContext("recon", ws) as ctx:
            raise ValueError("boom")
    except ValueError:
        pass
    # Should not crash; log file should exist with start event
    log_path = Path(ws) / "workflow.log"
    assert log_path.exists()
    content = log_path.read_text()
    assert "[AGENT] recon started" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/whitebox/tests/test_activity_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_whitebox.audit.activity_context'`

- [ ] **Step 3: Implement ActivityAuditContext**

Create `packages/whitebox/src/shannon_whitebox/audit/activity_context.py`:

```python
"""Per-activity audit context: creates WorkflowLogger, bridges events."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from shannon_core.logging import create_activity_logger
from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentLogDetails
from shannon_core.agents.workflow_event_bridge import WorkflowEventBridge
from .workflow_logger import WorkflowLogger

if TYPE_CHECKING:
    from shannon_core.logging.activity_logger import ActivityLogger


class ActivityAuditContext:
    """Async context manager for agent activities.

    Creates a WorkflowLogger pointing at the workspace's workflow.log,
    provides a WorkflowEventBridge for the executor, and logs agent
    start/end lifecycle events.

    Usage::

        async with ActivityAuditContext("recon", workspace_path, web_url) as ctx:
            metrics = await executor.execute(..., audit_logger=ctx.bridge)
            await ctx.log_agent_end(duration_ms=..., cost_usd=..., success=True)
    """

    def __init__(
        self,
        agent_name: str,
        workspace_path: str,
        web_url: str | None = None,
    ) -> None:
        self._agent_name = agent_name
        ws_dir = Path(workspace_path)
        self._meta = SessionMetadata(
            id=ws_dir.name,
            web_url=web_url,
            output_path=str(ws_dir.parent),
        )
        self._wf = WorkflowLogger(self._meta)
        self.bridge: WorkflowEventBridge | None = None
        self._activity_logger: ActivityLogger | None = None

    async def __aenter__(self) -> ActivityAuditContext:
        await self._wf.initialize()
        self._activity_logger = create_activity_logger()
        self.bridge = WorkflowEventBridge(
            self._activity_logger, self._wf, self._agent_name
        )
        await self._wf.log_agent(self._agent_name, "start")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # Ensure the log file is closed even on exception.
        # The caller should use log_agent_end() for the end event on the happy path.
        if self._wf is not None:
            try:
                await self._wf.close()
            except Exception:
                pass

    async def log_agent_end(
        self,
        duration_ms: int,
        cost_usd: float | None = None,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        """Write the [AGENT] ended event and close the logger."""
        details = AgentLogDetails(
            duration_ms=duration_ms,
            cost_usd=cost_usd,
            success=success,
            error=error,
        )
        await self._wf.log_agent(self._agent_name, "end", details)
        await self._wf.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/whitebox/tests/test_activity_context.py -v`
Expected: All 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/audit/activity_context.py packages/whitebox/tests/test_activity_context.py
git commit -m "feat(whitebox): add ActivityAuditContext for per-agent logging"
```

---

### Task 4: Wire run_agent with ActivityAuditContext

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:74-99`

Wrap `run_agent` (and by extension `run_vuln_agent` which delegates to it) with `ActivityAuditContext` so tool calls and agent lifecycle events flow to `workflow.log`.

- [ ] **Step 1: Write the failing test**

Add to `packages/whitebox/tests/test_activity_context.py`:

```python
async def test_run_agent_writes_workflow_log(tmp_path: Path, monkeypatch):
    """Integration: run_agent should create workflow.log with agent events."""
    from shannon_whitebox.pipeline.activities import run_agent
    from shannon_whitebox.pipeline.shared import ActivityInput
    from shannon_core.models.metrics import AgentMetrics

    repo = tmp_path / "target-repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    ws = tmp_path / "workspaces" / "recon"
    ws.mkdir(parents=True)

    mock_metrics = AgentMetrics(
        duration_ms=5000, cost_usd=0.01, num_turns=1,
        model="test", structured_output=None, stop_reason="end_turn",
    )

    async def mock_execute(self, **kwargs):
        # Simulate the bridge receiving a tool event
        bridge = kwargs.get("audit_logger")
        if bridge:
            await bridge.log_tool_start("Read", {"file_path": "/etc/hosts"})
        return mock_metrics

    monkeypatch.setattr(
        "shannon_core.agents.executor.AgentExecutor.execute", mock_execute
    )

    inp = ActivityInput(
        repo_path=str(repo),
        workspace_name="recon",
        workspace_path=str(ws),
    )
    result = await run_agent(inp)

    log_path = ws / "workflow.log"
    assert log_path.exists(), f"workflow.log not found at {log_path}"
    content = log_path.read_text()
    assert "[AGENT] recon started" in content
    assert "[TOOL] recon" in content
    assert "[AGENT] recon ended" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/test_activity_context.py::test_run_agent_writes_workflow_log -v`
Expected: FAIL — workflow.log is not created (current `run_agent` has no audit context).

- [ ] **Step 3: Modify `run_agent` to use ActivityAuditContext**

Replace `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` lines 74–99 with:

```python
@activity.defn
async def run_agent(input: ActivityInput) -> dict:
    import time as _time

    try:
        agent_name = AgentName(input.workspace_name)
        repo, deliverables, _ = _get_paths(input)

        # Determine workspace path for audit logging
        workspace_path = input.workspace_path or str(
            repo.parent / "workspaces" / (input.workspace_name or "default")
        )

        async with ActivityAuditContext(
            agent_name=agent_name.value,
            workspace_path=workspace_path,
            web_url=input.web_url,
        ) as audit:
            prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
            prompt_manager = PromptManager(prompts_dir)
            executor = AgentExecutor(prompt_manager)

            start_ms = _time.monotonic() * 1000
            metrics = await executor.execute(
                agent_name=agent_name,
                repo_path=str(repo),
                web_url=input.web_url,
                deliverables_path=str(deliverables),
                config_path=input.config_path,
                api_key=input.api_key,
                pipeline_testing=input.pipeline_testing_mode,
                prompt_override=input.prompt_override,
                audit_logger=audit.bridge,
            )

            elapsed_ms = _time.monotonic() * 1000 - start_ms
            await audit.log_agent_end(
                duration_ms=int(elapsed_ms),
                cost_usd=metrics.cost_usd,
                success=True,
            )
            return metrics.model_dump()

    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

Also add the import at the top of the file (after existing imports):

```python
from shannon_whitebox.audit.activity_context import ActivityAuditContext
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest packages/whitebox/tests/test_activity_context.py::test_run_agent_writes_workflow_log -v`
Expected: PASS.

- [ ] **Step 5: Run existing activity tests to verify nothing broke**

Run: `uv run pytest packages/whitebox/tests/ -v --timeout=30`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_activity_context.py
git commit -m "feat(whitebox): wire ActivityAuditContext into run_agent"
```

---

### Task 5: Phase Logging Activity

**Files:**
- Create: `packages/whitebox/src/shannon_whitebox/pipeline/phase_logger.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` (add import)
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:111-204` (call phase logger)
- Test: `packages/whitebox/tests/test_phase_logger.py`

A lightweight activity that appends `[PHASE]` lines to `workflow.log`. Called by the workflow before each agent execution.

- [ ] **Step 1: Write the failing test**

Create `packages/whitebox/tests/test_phase_logger.py`:

```python
from pathlib import Path

from shannon_whitebox.pipeline.phase_logger import log_phase_event
from shannon_whitebox.pipeline.shared import ActivityInput


async def test_log_phase_event_appends_line(tmp_path: Path):
    ws = tmp_path / "workspaces" / "test-ws"
    ws.mkdir(parents=True)
    log_file = ws / "workflow.log"
    log_file.write_text("[existing]\n", encoding="utf-8")

    inp = ActivityInput(
        repo_path=str(tmp_path / "repo"),
        workspace_path=str(ws),
        workspace_name="recon",
    )
    await log_phase_event(inp)

    content = log_file.read_text()
    assert content.startswith("[existing]\n")
    assert "[PHASE] recon" in content


async def test_log_phase_event_noop_without_workspace_path(tmp_path: Path):
    inp = ActivityInput(
        repo_path=str(tmp_path / "repo"),
        workspace_name="recon",
    )
    # Should not crash
    await log_phase_event(inp)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/test_phase_logger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_whitebox.pipeline.phase_logger'`

- [ ] **Step 3: Implement phase_logger**

Create `packages/whitebox/src/shannon_whitebox/pipeline/phase_logger.py`:

```python
"""Lightweight activity that appends [PHASE] lines to workflow.log."""

from __future__ import annotations

from pathlib import Path

import aiofiles
from temporalio import activity

from .shared import ActivityInput
from shannon_whitebox.audit.utils import format_log_time


@activity.defn
async def log_phase_event(input: ActivityInput) -> None:
    """Append a phase transition line to workflow.log.

    Uses workspace_path to locate the log file. No-ops if workspace_path
    is unset or the log file does not yet exist.
    """
    if not input.workspace_path:
        return
    log_path = Path(input.workspace_path) / "workflow.log"
    if not log_path.exists():
        return
    async with aiofiles.open(log_path, mode="a", encoding="utf-8") as f:
        await f.write(f"[{format_log_time()}] [PHASE] {input.workspace_name}\n")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest packages/whitebox/tests/test_phase_logger.py -v`
Expected: All 2 PASS.

- [ ] **Step 5: Wire phase logger into the workflow**

Modify `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`. Add import inside the `with workflow.unsafe.imports_passed_through():` block (after the existing `from . import activities` line):

```python
    from . import phase_logger
```

Then, before each agent execution block, add a phase log call. For example, before the pre-recon agent (around line 112):

```python
            # Log phase transition
            await workflow.execute_activity(
                phase_logger.log_phase_event,
                ActivityInput(**{**act_input.__dict__, "workspace_name": "pre-recon started"}),
                start_to_close_timeout=timedelta(seconds=10),
            )
```

Similarly before recon (around line 140), vulnerability-analysis (around line 165), and reporting (around line 198). Use descriptive phase names: `"pre-recon started"`, `"recon started"`, `"vulnerability-analysis started"`, `"reporting started"`.

- [ ] **Step 6: Run workflow tests**

Run: `uv run pytest packages/whitebox/tests/test_workflows.py -v`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/phase_logger.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/test_phase_logger.py
git commit -m "feat(whitebox): add phase logging activity and wire into workflow"
```

---

### Task 6: File Watcher — Replace Polling in worker.py

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`
- Delete: `packages/whitebox/tests/test_worker_progress.py` (tests removed polling function)
- Modify: `packages/whitebox/tests/test_worker.py` (update to match new flow)

Remove `poll_workflow_progress()`. Add `tail_log()` and `_AsyncLogHandler`. Wire into `run_scan()`.

- [ ] **Step 1: Write the failing test for `tail_log`**

Add to `packages/whitebox/tests/test_worker.py`:

```python
import asyncio
import sys
from pathlib import Path

from unittest.mock import AsyncMock, MagicMock, patch


async def test_tail_log_outputs_file_content(tmp_path, capsys):
    from shannon_whitebox.worker import tail_log

    log_path = tmp_path / "workflow.log"
    done = asyncio.Event()

    # Write content after a short delay
    async def write_later():
        await asyncio.sleep(0.3)
        log_path.write_text("[AGENT] recon started\n", encoding="utf-8")
        await asyncio.sleep(0.3)
        done.set()

    asyncio.create_task(write_later())
    await tail_log(log_path, done)

    output = capsys.readouterr().out
    assert "[AGENT] recon started" in output


async def test_tail_log_waits_for_file_to_appear(tmp_path, capsys):
    from shannon_whitebox.worker import tail_log

    log_path = tmp_path / "workflow.log"
    done = asyncio.Event()

    async def create_and_write():
        await asyncio.sleep(0.3)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("[PHASE] recon\n", encoding="utf-8")
        await asyncio.sleep(0.3)
        done.set()

    asyncio.create_task(create_and_write())
    await tail_log(log_path, done)

    output = capsys.readouterr().out
    assert "[PHASE] recon" in output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/test_worker.py::test_tail_log_outputs_file_content -v`
Expected: FAIL — `ImportError: cannot import name 'tail_log' from 'shannon_whitebox.worker'`

- [ ] **Step 3: Implement `tail_log` and `_AsyncLogHandler`**

Replace the entire contents of `packages/whitebox/src/shannon_whitebox/worker.py` with:

```python
import asyncio
import re
import sys
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .pipeline.activities import (
    log_phase_event,
    render_findings,
    run_agent,
    run_auth_validation,
    run_code_index,
    run_credential_check,
    run_preflight,
    run_rebuild_call_chains,
    run_render_dataflow_hints,
    run_risk_scoring,
    run_save_adjudication,
    run_vuln_agent,
)
from .pipeline.workflows import WhiteboxScanWorkflow
from .pipeline.shared import PipelineInput, PipelineProgress
from shannon_core.utils.paths import resolve_workspaces_dir
from shannon_core.services.temporal_infra import generate_task_queue

TASK_QUEUE_PREFIX = "shannon-py-wb"


class _AsyncLogHandler(FileSystemEventHandler):
    """Watchdog handler that prints new workflow.log content to stdout.

    Signals completion via an asyncio.Event when 'Workflow COMPLETED' or
    'Workflow FAILED' is detected in the log output.
    """

    _COMPLETION = re.compile(r"^Workflow (COMPLETED|FAILED)$", re.MULTILINE)

    def __init__(self, path: Path, done: asyncio.Event) -> None:
        self._path = path
        self._pos = 0
        self._done = done
        self._loop = asyncio.get_event_loop()

    def flush(self) -> bool:
        """Output new content since last read. Returns True on completion marker."""
        try:
            size = self._path.stat().st_size
            if size <= self._pos:
                return False
            content = self._path.read_text(encoding="utf-8")
            new = content[self._pos :]
            self._pos = size
            sys.stdout.write(new)
            sys.stdout.flush()
            return bool(self._COMPLETION.search(new))
        except Exception:
            return True  # File deleted/unreadable — treat as complete

    def on_modified(self, event) -> None:
        if event.src_path == str(self._path):
            if self.flush():
                self._loop.call_soon_threadsafe(self._done.set)


async def tail_log(log_path: Path, done: asyncio.Event) -> None:
    """Watch workflow.log and print new content to stdout.

    Waits for the file to appear, then uses watchdog to stream new lines.
    Stops when ``done`` is set or a completion marker is detected.
    """
    while not log_path.exists():
        if done.is_set():
            return
        await asyncio.sleep(0.5)

    handler = _AsyncLogHandler(log_path, done)
    if handler.flush():
        return

    observer = Observer()
    observer.schedule(handler, str(log_path.parent), recursive=False)
    observer.start()

    try:
        await done.wait()
    finally:
        observer.stop()
        observer.join(timeout=2)


async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233") -> dict:
    from shannon_core.session import SessionManager

    # Persist session data so blackbox can discover repo_path
    if input.workspace_name:
        workspaces_dir = resolve_workspaces_dir(input.repo_path)
        mgr = SessionManager(workspaces_dir)
        mgr.create_workspace(
            web_url=input.web_url or "",
            repo_path=input.repo_path,
            name=input.workspace_name,
        )

    # Determine log path for file watcher
    workspaces_dir = resolve_workspaces_dir(input.repo_path)
    if input.workspace_name:
        log_path = workspaces_dir / input.workspace_name / "workflow.log"
    else:
        log_path = Path(input.repo_path) / "workflow.log"

    client = await Client.connect(temporal_address)

    task_queue = generate_task_queue(TASK_QUEUE_PREFIX)

    worker = Worker(
        client=client,
        task_queue=task_queue,
        workflows=[WhiteboxScanWorkflow],
        activities=[
            log_phase_event,
            render_findings,
            run_agent,
            run_auth_validation,
            run_code_index,
            run_credential_check,
            run_preflight,
            run_rebuild_call_chains,
            run_render_dataflow_hints,
            run_risk_scoring,
            run_save_adjudication,
            run_vuln_agent,
        ],
    )

    async with worker:
        handle = await client.start_workflow(
            WhiteboxScanWorkflow.run,
            input,
            id=input.workspace_name or f"whitebox-{int(asyncio.get_event_loop().time())}",
            task_queue=task_queue,
        )

        done = asyncio.Event()
        tail_task = asyncio.create_task(tail_log(log_path, done))
        try:
            result = await handle.result()
            done.set()
            tail_task.cancel()
            try:
                await tail_task
            except asyncio.CancelledError:
                pass

            # Convert PipelineState to enriched dict for CLI consumption
            result_dict = asdict(result) if not isinstance(result, dict) else dict(result)
            result_dict["workspace_name"] = input.workspace_name
            result_dict["web_url"] = input.web_url

            workspaces_dir = resolve_workspaces_dir(input.repo_path)
            if input.workspace_name:
                result_dict["deliverables_path"] = str(
                    workspaces_dir / input.workspace_name / input.deliverables_subdir
                )
            else:
                result_dict["deliverables_path"] = str(
                    Path(input.repo_path) / input.deliverables_subdir
                )

            return result_dict
        except Exception:
            done.set()
            tail_task.cancel()
            try:
                await tail_task
            except asyncio.CancelledError:
                pass
            raise


def main():
    import sys
    asyncio.run(run_scan(PipelineInput(repo_path=sys.argv[1] if len(sys.argv) > 1 else ".")))
```

- [ ] **Step 4: Update existing worker tests**

The test `test_run_scan_persists_session_data` mocks `Worker` but not `Observer`. Update the patches to include the new imports. The mock handle already returns a completed state, so `tail_log` will work as long as `log_path` doesn't exist — it will poll, the `done` event will be set by the test flow, and it will exit.

No changes needed to `test_run_scan_persists_session_data` or `test_run_scan_uses_dynamic_task_queue` — they mock out Temporal, so the tail task exits immediately when `done.set()` is called from the exception path.

- [ ] **Step 5: Delete the polling test**

Delete `packages/whitebox/tests/test_worker_progress.py` — it tests the removed `poll_workflow_progress` function.

- [ ] **Step 6: Run all worker tests**

Run: `uv run pytest packages/whitebox/tests/test_worker.py -v`
Expected: All PASS (including new `test_tail_log_*` tests).

- [ ] **Step 7: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/worker.py packages/whitebox/tests/test_worker.py
git rm packages/whitebox/tests/test_worker_progress.py
git commit -m "feat(whitebox): replace polling with file watcher for real-time console output"
```

---

### Task 7: LLM Response Callback

**Files:**
- Modify: `packages/core/src/shannon_core/agents/message_dispatcher.py:29-34`
- Modify: `packages/core/src/shannon_core/agents/runner.py:134-136`
- Modify: `packages/core/src/shannon_core/agents/executor.py:25-37`

Add `llm_callback` to `MessageDispatcher` so assistant text responses are forwarded to `WorkflowLogger`. Wire it through the runner and executor.

- [ ] **Step 1: Write the failing test**

Create `packages/core/tests/test_llm_callback.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock


async def test_message_dispatcher_calls_llm_callback():
    from shannon_core.agents.message_dispatcher import MessageDispatcher

    callback = MagicMock()
    dispatcher = MessageDispatcher(llm_callback=callback)

    # Simulate an assistant event with text
    event = MagicMock()
    event.type = None  # not ResultMessage
    # Use assistant path
    block = MagicMock()
    block.text = "I found a vulnerability in the login form"
    event_with_type = MagicMock()
    event_with_type.type = "assistant"
    event_with_type.content = [block]
    event_with_type.error = None

    await dispatcher.dispatch(event_with_type)

    callback.assert_called_once()
    args = callback.call_args[0]
    assert args[0] == 1  # turn number
    assert "vulnerability" in args[1]


async def test_message_dispatcher_no_callback_when_none():
    from shannon_core.agents.message_dispatcher import MessageDispatcher

    dispatcher = MessageDispatcher(llm_callback=None)

    block = MagicMock()
    block.text = "test"
    event = MagicMock()
    event.type = "assistant"
    event.content = [block]
    event.error = None

    # Should not crash
    await dispatcher.dispatch(event)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_llm_callback.py -v`
Expected: FAIL — `MessageDispatcher.__init__() got an unexpected keyword argument 'llm_callback'`

- [ ] **Step 3: Add `llm_callback` to MessageDispatcher**

Modify `packages/core/src/shannon_core/agents/message_dispatcher.py`:

Add `llm_callback` parameter to `__init__` (line 29):

```python
    def __init__(
        self,
        audit_logger: ToolAuditLogger | None = None,
        progress_callback: Callable[[str], None] | None = None,
        error_callback: Callable[[str], None] | None = None,
        llm_callback: Callable[[int, str], None] | None = None,
    ) -> None:
        self.turn_count = 0
        self.text_parts: list[str] = []
        self.spending_cap_detected = False
        self.audit_logger: ToolAuditLogger = audit_logger or NullToolAuditLogger()
        self._progress = progress_callback
        self._on_error = error_callback
        self._llm_callback = llm_callback
        # L1: ResultMessage-level metadata collected in _handle_result_message
        self.result_is_error: bool = False
        self.result_subtype: str | None = None
        self.stop_reason: str | None = None
        self.permission_denials: list | None = None
        self.api_error_status: int | None = None
        self.result_errors: list[str] | None = None
```

In `_handle_assistant` (line 69), after appending text and checking spending cap, add:

```python
    async def _handle_assistant(self, event: Any) -> str:
        self.turn_count += 1
        for block in getattr(event, "content", []):
            if hasattr(block, "text"):
                text = block.text
                self.text_parts.append(text)
                if self._is_spending_cap_in_text(text):
                    self.spending_cap_detected = True
                if self._llm_callback and text:
                    self._llm_callback(self.turn_count, text[:200])
        error = getattr(event, "error", None)
        if error and self._on_error:
            self._on_error(str(error))
        return "continue"
```

- [ ] **Step 4: Wire `llm_callback` through runner.py**

Modify `packages/core/src/shannon_core/agents/runner.py`. Add `llm_callback` parameter to `run_claude_prompt` (after `audit_logger` at line 98):

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
    llm_callback: "Callable[[int, str], None] | None" = None,
) -> ClaudeRunResult:
```

Then pass it to `provider.call()` (around line 138):

```python
        result = await provider.call(
            prompt=prompt,
            cwd=repo_path,
            model_tier=model_tier,
            output_format=output_format,
            deliverables_subdir=deliverables_subdir,
            audit_logger=tool_audit_logger,
            llm_callback=llm_callback,
        )
```

- [ ] **Step 5: Wire `llm_callback` through executor.py**

Modify `packages/core/src/shannon_core/agents/executor.py`. Add `llm_callback` parameter to `execute` (after `audit_logger` at line 37):

```python
    async def execute(
        self,
        agent_name: AgentName,
        repo_path: str,
        web_url: str = "",
        deliverables_path: str | None = None,
        config_path: str | None = None,
        api_key: str | None = None,
        pipeline_testing: bool = False,
        prompt_variables: dict[str, str] | None = None,
        prompt_override: str | None = None,
        structured_output_schema: dict | None = None,
        audit_logger: "ActivityLogger | None" = None,
        llm_callback: "Callable[[int, str], None] | None" = None,
    ) -> AgentMetrics:
```

Then pass it to `run_claude_prompt` (around line 65):

```python
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=str(repo),
            model_tier=defn.model_tier,
            api_key=api_key,
            deliverables_subdir=str(deliverables.relative_to(repo)) if deliverables.is_relative_to(repo) else None,
            structured_output_schema=structured_output_schema,
            audit_logger=audit_logger,
            llm_callback=llm_callback,
        )
```

- [ ] **Step 6: Wire the callback in ActivityAuditContext**

Modify `packages/whitebox/src/shannon_whitebox/audit/activity_context.py`. Add a method that creates the LLM callback:

```python
    def make_llm_callback(self):
        """Create an LLM callback for the executor that writes to WorkflowLogger."""
        async def _callback(turn: int, content: str) -> None:
            await self._wf.log_llm_response(self._agent_name, turn, content)

        return _callback
```

Then update `run_agent` in `activities.py` to pass the callback:

```python
            metrics = await executor.execute(
                agent_name=agent_name,
                repo_path=str(repo),
                web_url=input.web_url,
                deliverables_path=str(deliverables),
                config_path=input.config_path,
                api_key=input.api_key,
                pipeline_testing=input.pipeline_testing_mode,
                prompt_override=input.prompt_override,
                audit_logger=audit.bridge,
                llm_callback=audit.make_llm_callback(),
            )
```

- [ ] **Step 7: Run all new and existing tests**

Run: `uv run pytest packages/core/tests/test_llm_callback.py packages/whitebox/tests/ -v --timeout=30`
Expected: All PASS.

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/shannon_core/agents/message_dispatcher.py packages/core/src/shannon_core/agents/runner.py packages/core/src/shannon_core/agents/executor.py packages/whitebox/src/shannon_whitebox/audit/activity_context.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/core/tests/test_llm_callback.py
git commit -m "feat(core): add LLM response callback through executor pipeline"
```

---

### Task 8: End-to-End Verification

**Files:** No new code files. Smoke test the full pipeline.

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest packages/ -v --timeout=30`
Expected: All PASS. If any test fails, fix before proceeding.

- [ ] **Step 2: Manual smoke test with pipeline-testing mode**

Run:
```bash
uv run shannon-whitebox start -r /Users/mango/project/vuln-range/NodeGoat --pipeline-testing
```

Expected console output should show:
```
================================================================================
Shannon Pentest - Workflow Log
================================================================================
...

[YYYY-MM-DD HH:MM:SS] [PHASE] pre-recon started
[YYYY-MM-DD HH:MM:SS] [AGENT] pre-recon started
[YYYY-MM-DD HH:MM:SS] [TOOL] pre-recon → Read(file_path=...)
[YYYY-MM-DD HH:MM:SS] [AGENT] pre-recon ended (duration: ..., cost: ..., ✓)
...
```

NOT the old repetitive `[0s] Phase: unknown | Agent: none | Completed: 0/13` output.

- [ ] **Step 3: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: address issues from e2e verification of realtime console logging"
```

---

## Self-Review

### Spec Coverage

| Spec Section | Task |
|---|---|
| 1. WorkflowEventBridge | Task 2 |
| 2. ActivityAuditContext | Task 3 |
| 3. WorkflowLogger Header Detection | Task 1 |
| 4. LogStream isEmpty | Removed — `path.stat().st_size == 0` used directly in Task 1, no separate method needed |
| 5. Activity Changes (run_agent) | Task 4 |
| 6. Phase Logging | Task 5 |
| 7. File Watcher in run_scan | Task 6 |
| 8. LLM Response Capture | Task 7 |

### Placeholder Scan

No TBDs, TODOs, or vague steps. Every step has exact code and commands.

### Type Consistency

- `ActivityAuditContext.__init__` takes `(agent_name: str, workspace_path: str, web_url: str | None)` — matches usage in Task 4 (`agent_name=agent_name.value`, `workspace_path=workspace_path`, `web_url=input.web_url`).
- `WorkflowEventBridge.__init__` takes `(activity_logger, workflow_logger, agent_name)` — matches creation in `ActivityAuditContext.__aenter__`.
- `tail_log(log_path: Path, done: asyncio.Event)` — matches usage in `run_scan`.
- `ActivityInput.workspace_path: str | None` — used in `log_phase_event`, checked with `if not input.workspace_path: return`.
- `llm_callback: Callable[[int, str], None]` — matches signature in `MessageDispatcher`, `runner.py`, `executor.py`, and `ActivityAuditContext.make_llm_callback()`.
