# Audit Logging System - Phase 1: Core Class Alignment

**Date:** 2026-06-03
**Status:** Approved
**Scope:** Refactor audit package to match original Shannon (TypeScript) logging architecture

## Problem

The Python rewrite (`/root/shannon-py`) has a skeletal audit system: only `LogStream` and a flat `AuditSession` that writes plain-text messages with timestamps. The original Shannon project has a rich, layered audit system with:

- Separate `AgentLogger` (JSON Lines) and `WorkflowLogger` (human-readable) classes
- A `MetricsTracker` that persists structured metrics to `session.json`
- Crash-safe append-only streams with backpressure handling
- Concurrency control via `SessionMutex`
- Structured headers, tool-call formatting, and workflow summary blocks

The Python version is missing all of these. Worse, `AuditSession` is imported in the pipeline but never instantiated, so no audit logging actually happens at runtime.

## Goal

Bring the Python audit package up to parity with the original TypeScript implementation by introducing the same layered architecture, without yet integrating it into the pipeline (that is Phase 2).

## Target File Structure

```
packages/whitebox/src/shannon_whitebox/audit/
├── __init__.py          # Export AuditSession
├── log_stream.py        # Enhanced: persistent stream, open/write/close lifecycle
├── agent_logger.py      # NEW: JSON Lines agent log with header
├── workflow_logger.py   # NEW: Human-readable workflow log with categories
├── metrics_tracker.py   # NEW: session.json management with atomic read/write
├── session.py           # Simplified to facade coordinating the three components
└── utils.py             # NEW: Path generation + formatting utilities
```

## Component Designs

### 1. `utils.py` — Path Generation & Formatting

Pure utility functions. No side effects.

```python
def format_duration(ms: int) -> str:
    """Convert milliseconds to human-readable: '23ms', '1.5s', '2m 30s'."""

def format_timestamp(ts: float | None = None) -> str:
    """ISO 8601 UTC string. Defaults to now."""

def format_log_time() -> str:
    """Human-readable local format 'YYYY-MM-DD HH:MM:SS' for workflow.log lines."""

def sanitize_hostname(url: str) -> str:
    """Extract and sanitize hostname from URL for identifiers."""

def generate_audit_path(meta: SessionMetadata) -> Path
def generate_log_path(meta: SessionMetadata, agent_name: str, timestamp: int, attempt: int) -> Path
def generate_prompt_path(meta: SessionMetadata, agent_name: str) -> Path
def generate_workflow_log_path(meta: SessionMetadata) -> Path
def generate_session_json_path(meta: SessionMetadata) -> Path
def initialize_audit_structure(meta: SessionMetadata) -> None
    """Create workspaces/{id}/, agents/, prompts/, deliverables/ directories."""
```

### 2. `log_stream.py` — Enhanced LogStream

**Change:** Shift from "open file per append" to persistent stream lifecycle.

```python
class LogStream:
    def __init__(self, file_path: Path)
    async def open(self) -> None           # Create dirs, open in append mode
    async def write(self, text: str) -> None  # Raw write, no timestamp prefix
    async def close(self) -> None          # Flush and close
    @property
    def is_open(self) -> bool
    @property
    def path(self) -> Path
```

Key changes from current implementation:
- `open()` / `close()` lifecycle instead of opening per-call
- `write()` writes raw text (caller controls formatting)
- Error handling: catch and log stream errors, set `is_open = False`
- Keep `append(line: str)` as backward-compat helper that adds `[timestamp] {line}\n`

### 3. `agent_logger.py` — NEW AgentLogger

Corresponds to `audit/logger.ts` in the original project.

```python
class AgentLogger:
    def __init__(self, session_metadata: SessionMetadata, agent_name: str, attempt_number: int)
    async def initialize(self) -> None
    async def log_event(self, event_type: str, event_data: Any) -> None
    async def close(self) -> None

    @staticmethod
    async def save_prompt(session_metadata: SessionMetadata, agent_name: str, content: str) -> None
```

Agent log file format (JSON Lines after a text header):

```
========================================
Agent: recon
Attempt: 1
Started: 2026-06-03T10:30:00.000Z
Session: session-id
Web URL: https://example.com
========================================

{"type":"agent_start","timestamp":"2026-06-03T10:30:00.000Z","data":{"agentName":"recon","attemptNumber":1}}
{"type":"tool_start","timestamp":"2026-06-03T10:30:05.000Z","data":{"toolName":"Read","parameters":{"file_path":"/path"}}}
{"type":"agent_end","timestamp":"2026-06-03T10:35:00.000Z","data":{"success":true,"duration_ms":300000}}
```

Agent log file naming: `{timestamp}_{agentName}_attempt-{N}.log` in `agents/` subdirectory.

Prompt snapshots: Markdown format with metadata header, saved via atomic write.

### 4. `workflow_logger.py` — NEW WorkflowLogger

Corresponds to `audit/workflow-logger.ts` in the original project.

```python
class WorkflowLogger:
    def __init__(self, session_metadata: SessionMetadata)
    async def initialize(self, workflow_id: str | None = None) -> None
    async def log_phase(self, phase: str, event: Literal["start", "complete"]) -> None
    async def log_agent(self, agent_name: str, event: Literal["start", "end"], details: AgentLogDetails | None = None) -> None
    async def log_tool_start(self, agent_name: str, tool_name: str, parameters: Any) -> None
    async def log_llm_response(self, agent_name: str, turn: int, content: str) -> None
    async def log_event(self, event_type: str, message: str) -> None
    async def log_error(self, error: Exception, context: str | None = None) -> None
    async def log_workflow_complete(self, summary: WorkflowSummary) -> None
    async def log_resume_header(self, resume_info: ResumeInfo) -> None
    async def close(self) -> None
```

Workflow log line format: `[YYYY-MM-DD HH:MM:SS] [CATEGORY] message`

Header on initialization:
```
================================================================================
Shannon Pentest - Workflow Log
================================================================================
Workflow ID: {id}
Target URL:  {url}
Started:     {timestamp}
================================================================================
```

Summary on completion (single atomic write):
```
================================================================================
Workflow COMPLETED
────────────────────────────────────────
Workflow ID: {id}
Status:      completed
Duration:    5m 30s
Total Cost:  $0.1234
Agents:      5 completed

Agent Breakdown:
  - recon (2m 30s, $0.0567)
  - injection-vuln (1m 15s, $0.0234)
================================================================================
```

Tool parameter formatting: per-tool smart truncation (Bash → truncate command, Read → show path, Grep → show pattern, etc.).

### 5. `metrics_tracker.py` — NEW MetricsTracker

Corresponds to `audit/metrics-tracker.ts` in the original project.

```python
class MetricsTracker:
    def __init__(self, session_metadata: SessionMetadata)
    async def initialize(self, workflow_id: str | None = None) -> None
    def start_agent(self, agent_name: str, attempt_number: int) -> None
    async def end_agent(self, agent_name: str, result: AgentEndResult) -> None
    async def update_session_status(self, status: str) -> None
    async def add_resume_attempt(self, workflow_id: str, terminated: list[str], checkpoint: str | None = None) -> None
    async def reload(self) -> None
    def get_metrics(self) -> dict
```

session.json structure (matches original):
```json
{
  "session": {
    "id": "session-id",
    "webUrl": "https://example.com",
    "status": "in-progress",
    "createdAt": "2026-06-03T10:30:00.000Z",
    "originalWorkflowId": "wf-123",
    "resumeAttempts": []
  },
  "metrics": {
    "total_duration_ms": 0,
    "total_cost_usd": 0,
    "phases": {},
    "agents": {}
  }
}
```

Atomic write: write to temp file, then `os.replace()`.
Phase aggregation: group agents by phase, compute duration percentage.

### 6. `session.py` — Simplified AuditSession Facade

Change from "does everything" to "coordinates three components".

```python
class AuditSession:
    def __init__(self, session_metadata: SessionMetadata)
    async def initialize(self, workflow_id: str | None = None) -> None
    async def start_agent(self, agent_name: str, prompt: str, attempt: int = 1) -> None
    async def log_event(self, event_type: str, event_data: Any) -> None
    async def end_agent(self, agent_name: str, result: AgentEndResult) -> None
    async def log_phase_start(self, phase: str) -> None
    async def log_phase_complete(self, phase: str) -> None
    async def log_workflow_complete(self, summary: WorkflowSummary) -> None
    async def update_session_status(self, status: str) -> None
    async def add_resume_attempt(self, workflow_id: str, terminated: list[str], checkpoint: str | None = None) -> None
    async def log_resume_header(self, resume_info: ResumeInfo) -> None
    async def get_metrics(self) -> dict
```

Internal state:
- `_agent_logger: AgentLogger | None` — current agent's logger
- `_workflow_logger: WorkflowLogger` — shared workflow log
- `_metrics_tracker: MetricsTracker` — session metrics
- `_lock: asyncio.Lock` — protects session.json writes
- `_current_agent_name: str | None`

`log_event()` dispatches to both agent log (JSON) and workflow log (human-readable):
- `tool_start` → `WorkflowLogger.log_tool_start()`
- `llm_response` → `WorkflowLogger.log_llm_response()`

`end_agent()` acquires `_lock`, calls `_metrics_tracker.reload()` then `end_agent()`, releases lock.

## Data Types

Add to `shannon_core/models/metrics.py` or a new `shannon_core/models/audit.py`:

```python
class SessionMetadata(BaseModel):
    id: str
    web_url: str | None = None
    repo_path: str | None = None
    output_path: str | None = None
    model_config = ConfigDict(extra="allow")

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

## Out of Scope (Phase 2+)

- Pipeline integration (instantiating AuditSession in activities.py)
- Blackbox package audit integration
- CLI `logs` command enhancement
- Actual resume workflow invocation

## Dependencies

- `aiofiles` (already in project) for async file I/O
- No new external dependencies required

## Testing Strategy

- Unit tests per component: `test_log_stream.py`, `test_agent_logger.py`, `test_workflow_logger.py`, `test_metrics_tracker.py`, `test_audit_session.py`
- Integration test: full lifecycle (initialize → start_agent → log_events → end_agent → log_workflow_complete)
- Use `tmp_path` fixture for file system isolation
