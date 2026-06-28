# Real-time Console Logging for Shannon-Py

**Date**: 2026-06-10
**Status**: Draft
**Scope**: whitebox, core

## Problem

The shannon-py CLI only shows repetitive polling output during white-box scans:

```
[0s] Phase: unknown | Agent: none | Completed: 0/13
[2s] Phase: pre-recon | Agent: pre-recon | Completed: 0/13
[2s] Phase: pre-recon | Agent: pre-recon | Completed: 0/13
```

The original shannon (TypeScript) shows rich real-time events:

```
[2025-06-10 10:30:16] [AGENT] pre-recon: Starting (attempt 1)
[2025-06-10 10:30:17] [pre-recon] [TOOL] Bash: npm audit --json
[2025-06-10 10:30:25] [pre-recon] [TOOL] Read: /path/to/package.json
[2025-06-10 10:31:45] [AGENT] pre-recon: Completed (2m29s, $0.12)
```

Two root causes:

1. **AuditSession/WorkflowLogger is not connected to whitebox activities** — `workflow.log` is empty or doesn't exist during scans.
2. **CLI `start` command doesn't watch `workflow.log`** — only polls Temporal for basic progress.

## Architecture

### Current Flow (Broken)

```
Activity → Executor → MessageDispatcher → ActivityToolAuditLogger → Temporal context log
                                                                                   ↓
                                                                          (lost, not displayed)
```

### New Flow

```
Activity → ActivityAuditContext ──→ WorkflowLogger ──→ workflow.log ──→ FileWatcher → Console
                         ↓                                   ↑
        AgentExecutor → MessageDispatcher ─────────────────┘
                         ↓
                  ActivityToolAuditLogger → Temporal context log (preserved)
```

Key principles:

- **Preserve existing Temporal logging** — ActivityToolAuditLogger still writes to Temporal context.
- **Append-only writes** — Multiple activities write to the same `workflow.log` via append mode.
- **Decoupled display** — File watcher reads independently; writer and reader don't interact.

## Components

### 1. WorkflowEventBridge (new file)

**File**: `packages/core/src/shannon_core/agents/workflow_event_bridge.py`

A `ToolAuditLogger` implementation that bridges tool events to both the existing ActivityLogger and a WorkflowLogger.

```python
class WorkflowEventBridge(ActivityToolAuditLogger):
    """Bridges tool audit events to ActivityLogger AND WorkflowLogger."""

    def __init__(
        self,
        activity_logger: ActivityLogger,
        workflow_logger: WorkflowLogger,
        agent_name: str,
    ):
        super().__init__(activity_logger)
        self._wf = workflow_logger
        self._agent = agent_name

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        await super().log_tool_start(tool_name, parameters)
        await self._wf.log_tool_start(self._agent, tool_name, parameters)

    async def log_tool_end(self, result: Any) -> None:
        await super().log_tool_end(result)

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        await super().log_error(error, turn_count=turn_count, duration_ms=duration_ms)
        await self._wf.log_error(Exception(error), context=f"turn {turn_count}")
```

### 2. ActivityAuditContext (new file)

**File**: `packages/whitebox/src/shannon_whitebox/audit/activity_context.py`

A context manager for agent activities. Creates a WorkflowLogger, provides a WorkflowEventBridge for the executor, and logs agent lifecycle events.

```python
class ActivityAuditContext:
    """Per-activity audit context: creates WorkflowLogger, logs agent lifecycle."""

    def __init__(self, agent_name: str, workspace_path: str, web_url: str | None = None):
        # workspace_path is the workspace directory, e.g. workspaces/juice-shop_whitebox-123/
        # SessionMetadata.id = workspace directory name; output_path = workspace parent dir
        # This ensures generate_workflow_log_path() → {workspaces_dir}/{workspace_name}/workflow.log
        self._agent_name = agent_name
        ws_dir = Path(workspace_path)
        meta = SessionMetadata(
            id=ws_dir.name,              # e.g. "juice-shop_whitebox-123"
            web_url=web_url,
            output_path=str(ws_dir.parent),  # e.g. "workspaces"
        )
        self._wf = WorkflowLogger(meta)
        self.bridge: WorkflowEventBridge | None = None
        self._activity_logger: ActivityLogger | None = None

    async def __aenter__(self) -> "ActivityAuditContext":
        await self._wf.initialize(write_header=True)  # skips header if file exists
        self._activity_logger = create_activity_logger()
        self.bridge = WorkflowEventBridge(self._activity_logger, self._wf, self._agent_name)
        await self._wf.log_agent(self._agent_name, "start")
        return self

    async def __aexit__(self, *exc):
        # Log end event with whatever metrics are available
        await self._wf.close()

    async def log_agent_end(self, duration_ms: int, cost_usd: float | None = None,
                            success: bool = True, error: str | None = None) -> None:
        details = AgentLogDetails(
            duration_ms=duration_ms,
            cost_usd=cost_usd,
            success=success,
            error=error,
        )
        await self._wf.log_agent(self._agent_name, "end", details)
        await self._wf.close()
```

### 3. WorkflowLogger Header Detection (modify)

**File**: `packages/whitebox/src/shannon_whitebox/audit/workflow_logger.py`

**Change**: Add `write_header` parameter and file-size check. When an activity opens an existing `workflow.log`, it skips the header and just appends events.

```python
async def initialize(self, workflow_id: str | None = None, write_header: bool = True) -> None:
    path = generate_workflow_log_path(self._meta)
    self._stream = LogStream(path)
    await self._stream.open()
    if write_header:
        # Only write header if file is new (empty after open)
        stat = path.stat()
        if stat.st_size == 0:
            await self._write_header(workflow_id)
```

Also add a `_write_header()` private method extracted from the current `initialize()` body.

### 4. LogStream isEmpty Check (modify)

**File**: `packages/whitebox/src/shannon_whitebox/audit/log_stream.py`

**Change**: Add a method to check if the underlying file is empty.

```python
@property
def is_empty(self) -> bool:
    if self._path is None:
        return True
    try:
        return self._path.stat().st_size == 0
    except OSError:
        return True
```

### 5. Activity Changes (modify)

**File**: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`

**Change in `run_agent`**: Wrap executor execution with `ActivityAuditContext`.

```python
@activity.defn
async def run_agent(input: ActivityInput) -> dict:
    try:
        agent_name = AgentName(input.workspace_name)
        repo, deliverables, _ = _get_paths(input)

        # Determine workspace path for audit logging
        workspace_path = input.workspace_path or str(repo.parent / "workspaces" / input.workspace_name)

        async with ActivityAuditContext(
            agent_name=agent_name.value,
            workspace_path=workspace_path,
            web_url=input.web_url,
        ) as audit:
            prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
            prompt_manager = PromptManager(prompts_dir)
            executor = AgentExecutor(prompt_manager)

            start_ms = time.monotonic() * 1000
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

            elapsed_ms = time.monotonic() * 1000 - start_ms
            await audit.log_agent_end(
                duration_ms=int(elapsed_ms),
                cost_usd=metrics.get("cost_usd"),
                success=True,
            )
            return metrics

    except PentestError as e:
        ...
    except Exception as e:
        ...
```

### 6. Phase Logging (modify)

**File**: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

**Change**: The workflow already tracks `current_phase` transitions. To log phase events to `workflow.log`, add a lightweight "log phase" activity or use the workflow's existing signal mechanism.

The simplest approach: create a `log_phase_activity` that appends a single line to `workflow.log`.

```python
@activity.defn
async def log_phase_event(input: ActivityInput) -> None:
    """Append a phase transition event to workflow.log."""
    workspace_path = input.workspace_path
    if not workspace_path:
        return
    log_path = Path(workspace_path) / "workflow.log"
    if log_path.exists():
        async with aiofiles.open(log_path, mode="a") as f:
            await f.write(f"[{format_log_time()}] [PHASE] {input.workspace_name}\n")
```

In the workflow, call this before each agent execution:

```python
await workflow.execute_activity(
    activities.log_phase_event,
    ActivityInput(**{**act_input.__dict__, "workspace_name": "pre-recon started"}),
    start_to_close_timeout=timedelta(seconds=10),
)
```

### 7. File Watcher in run_scan (modify)

**File**: `packages/whitebox/src/shannon_whitebox/worker.py`

**Changes**:
- Remove `poll_workflow_progress()` entirely.
- Add `tail_log()` async function using `watchdog`.
- In `run_scan()`, start the file watcher alongside the Temporal worker.

```python
async def tail_log(log_path: Path, done: asyncio.Event) -> None:
    """Watch workflow.log and print new content to stdout. Stops on done event."""
    # Wait for file to appear
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


class _AsyncLogHandler(FileSystemEventHandler):
    """File event handler that prints new log content and signals on completion."""

    COMPLETION = re.compile(r"^Workflow (COMPLETED|FAILED)$", re.MULTILINE)

    def __init__(self, path: Path, done: asyncio.Event):
        self._path = path
        self._pos = 0
        self._done = done
        self._loop = asyncio.get_event_loop()

    def flush(self) -> bool:
        try:
            size = self._path.stat().st_size
            if size <= self._pos:
                return False
            content = self._path.read_text(encoding="utf-8")
            new = content[self._pos:]
            self._pos = size
            sys.stdout.write(new)
            sys.stdout.flush()
            return bool(self.COMPLETION.search(new))
        except Exception:
            return True

    def on_modified(self, event) -> None:
        if event.src_path == str(self._path):
            if self.flush():
                self._loop.call_soon_threadsafe(self._done.set)
```

In `run_scan()`:

```python
async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233") -> dict:
    # ... existing workspace setup ...

    # Determine log path — must match generate_workflow_log_path():
    #   output_path / meta.id / workflow.log  where output_path = workspaces_dir, id = workspace_name
    workspaces_dir = resolve_workspaces_dir(input.repo_path)
    if input.workspace_name:
        log_path = workspaces_dir / input.workspace_name / "workflow.log"
    else:
        log_path = Path(input.repo_path) / "workflow.log"

    client = await Client.connect(temporal_address)
    task_queue = generate_task_queue(TASK_QUEUE_PREFIX)
    worker = Worker(client=client, task_queue=task_queue, ...)

    async with worker:
        handle = await client.start_workflow(...)

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
            # ... existing result processing ...
        except Exception:
            done.set()
            tail_task.cancel()
            ...
```

### 8. LLM Response Capture (modify)

**File**: `packages/core/src/shannon_core/agents/message_dispatcher.py`

**Change**: Add `llm_callback` parameter. When the dispatcher receives an assistant text response, invoke the callback.

```python
def __init__(
    self,
    audit_logger: ToolAuditLogger | None = None,
    progress_callback: Callable[[str], None] | None = None,
    error_callback: Callable[[str], None] | None = None,
    llm_callback: Callable[[int, str], None] | None = None,  # NEW: (turn, content)
) -> None:
```

In the message processing loop, when an assistant text block is received:

```python
if llm_callback and text_content:
    llm_callback(turn_number, text_content[:200])
```

Wire this callback through the executor to the WorkflowLogger:

```python
# In executor.execute()
if audit_logger and isinstance(audit_logger, WorkflowEventBridge):
    llm_callback = lambda turn, content: asyncio.ensure_future(
        audit_logger.wf_logger.log_llm_response(agent_name, turn, content)
    )
else:
    llm_callback = None
```

## Implementation Order

1. **WorkflowLogger header detection** (smallest change, enabler)
2. **LogStream.isEmpty** (needed by header detection)
3. **WorkflowEventBridge** (bridges tool events)
4. **ActivityAuditContext** (ties it together)
5. **Modify `run_agent`** to use ActivityAuditContext
6. **Phase logging activity** (optional, lower priority)
7. **File watcher in `run_scan`** (replaces polling)
8. **LLM callback** (enriches output, lower priority)

## Out of Scope

- Blackbox side — same pattern, implement after whitebox is verified.
- ANSI color support — can be added as a post-processing layer later.
- Full AuditSession with AgentLogger/MetricsTracker — follow-up task.
- Modifying the `logs --follow` command — it already works correctly.

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Multiple activities writing to same file simultaneously | LogStream uses `aiofiles` in append mode; OS guarantees atomic appends for small writes. |
| workflow.log doesn't exist when watcher starts | `tail_log()` polls for file existence before starting watchdog observer. |
| Watchdog thread + async event loop | Use `loop.call_soon_threadsafe(done.set)` for thread-safe signaling. |
| Header written multiple times | File size check in `WorkflowLogger.initialize()` — only write header if file is empty. |
| Activity fails mid-execution | `ActivityAuditContext.__aexit__` ensures `wf.close()` is called. |
