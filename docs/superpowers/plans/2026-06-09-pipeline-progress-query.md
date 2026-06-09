# Pipeline Progress Query Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Temporal query handlers to whitebox and blackbox workflows so CLI can display real-time progress during scans.

**Architecture:** Add `PipelineProgress` dataclass and `@workflow.query` handler to both workflows. Update state tracking during workflow execution.

**Tech Stack:** Python, Temporal SDK, dataclasses

---

## File Structure

```
packages/whitebox/src/shannon_whitebox/pipeline/
├── shared.py          # Add PipelineProgress dataclass, add current_agent field to PipelineState
└── workflows.py       # Add @workflow.query handler, update state during execution

packages/blackbox/src/shannon_blackbox/pipeline/
├── shared.py          # Add PipelineProgress dataclass, add current_agent field to BlackboxPipelineState
└── workflows.py       # Add @workflow.query handler, update state during execution
```

---

## Task 1: Add PipelineProgress Dataclass to Whitebox

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`

- [ ] **Step 1: Add PipelineProgress dataclass after PipelineState**

Add this at the end of the file:

```python
@dataclass
class PipelineProgress:
    """工作流进度查询返回值。"""
    workflow_id: str
    elapsed_ms: int
    current_phase: str | None
    current_agent: str | None
    completed_agents: list[str]
    status: str
```

- [ ] **Step 2: Run Python syntax check**

Run: `python -m py_compile packages/whitebox/src/shannon_whitebox/pipeline/shared.py`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/shared.py
git commit -m "feat(whitebox): add PipelineProgress dataclass"
```

---

## Task 2: Add current_agent Field to Whitebox PipelineState

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`

- [ ] **Step 1: Add current_agent field to PipelineState**

Find the `PipelineState` dataclass and add the field:

```python
@dataclass
class PipelineState:
    status: str = "running"
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    start_time: float = 0.0
    errors: list[str] = field(default_factory=list)
    code_index_stats: dict | None = None
    audit_plan_stats: dict | None = None
    error_code: str | None = None
    failed_agents: list[str] = field(default_factory=list)
    current_phase: str | None = None
    current_agent: str | None = None
```

- [ ] **Step 2: Run Python syntax check**

Run: `python -m py_compile packages/whitebox/src/shannon_whitebox/pipeline/shared.py`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/shared.py
git commit -m "feat(whitebox): add current_phase and current_agent to PipelineState"
```

---

## Task 3: Add Query Handler to Whitebox Workflow

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

- [ ] **Step 1: Import PipelineProgress**

Add import at the top with the other imports from `.shared`:

```python
from .shared import ActivityInput, PipelineInput, PipelineState, PipelineProgress
```

- [ ] **Step 2: Add query handler method to WhiteboxScanWorkflow**

Add this method inside the `WhiteboxScanWorkflow` class (after the `run` method):

```python
@workflow.query
def pipeline_progress(self) -> PipelineProgress:
    """返回当前工作流进度供 CLI 轮询。"""
    from temporalio import workflow as wf

    elapsed_ns = wf.time_ns() - int(self._state.start_time * 1e9)

    return PipelineProgress(
        workflow_id=wf.info().workflow_id,
        elapsed_ms=elapsed_ns // 1_000_000,
        current_phase=self._state.current_phase,
        current_agent=self._state.current_agent,
        completed_agents=self._state.completed_agents,
        status=self._state.status,
    )
```

- [ ] **Step 3: Run Python syntax check**

Run: `python -m py_compile packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`
Expected: No output (success)

- [ ] **Step 4: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
git commit -m "feat(whitebox): add pipeline_progress query handler"
```

---

## Task 4: Update State Tracking in Whitebox Workflow

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

- [ ] **Step 1: Add pre-recon phase tracking**

Find the pre-recon section and add state updates:

```python
if AgentName.PRE_RECON.value not in self._state.completed_agents:
    self._state.current_phase = "pre-recon"
    self._state.current_agent = AgentName.PRE_RECON.value
    pre_recon_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value})
    metrics = await workflow.execute_activity(
        activities.run_agent, pre_recon_input,
        start_to_close_timeout=timedelta(hours=2),
        retry_policy=PRODUCTION_RETRY,
    )
    self._state.completed_agents.append(AgentName.PRE_RECON.value)
    self._state.agent_metrics[AgentName.PRE_RECON.value] = metrics
    self._state.current_agent = None
```

- [ ] **Step 2: Add recon phase tracking**

Find the recon section and add state updates:

```python
if AgentName.RECON.value not in self._state.completed_agents:
    self._state.current_phase = "recon"
    self._state.current_agent = AgentName.RECON.value
    recon_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.RECON.value})
    metrics = await workflow.execute_activity(
        activities.run_agent, recon_input,
        start_to_close_timeout=timedelta(hours=2),
    )
    self._state.completed_agents.append(AgentName.RECON.value)
    self._state.agent_metrics[AgentName.RECON.value] = metrics
    self._state.current_agent = None
```

- [ ] **Step 3: Add vuln agents phase tracking**

Find the vuln tasks loop and add state updates before the loop:

```python
vuln_tasks = []
self._state.current_phase = "vulnerability-analysis"
for vt in selected_classes:
    agent_name = AgentName(f"{vt}-vuln")
    if agent_name.value not in self._state.completed_agents:
        self._state.current_agent = agent_name.value
        # ... existing code ...
```

- [ ] **Step 4: Add reporting phase tracking**

Find the render_findings activity call and add state updates:

```python
self._state.current_phase = "reporting"
self._state.current_agent = "render-findings"
await workflow.execute_activity(
    activities.render_findings, act_input,
    start_to_close_timeout=timedelta(minutes=5),
)
self._state.current_agent = None
```

- [ ] **Step 5: Reset state at completion**

Before returning the state, add:

```python
if self._state.failed_agents:
    self._state.status = "failed"
    # ... existing code ...
else:
    self._state.status = "completed"
self._state.current_phase = None
return self._state
```

- [ ] **Step 6: Run Python syntax check**

Run: `python -m py_compile packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`
Expected: No output (success)

- [ ] **Step 7: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
git commit -m "feat(whitebox): update state tracking during workflow execution"
```

---

## Task 5: Add PipelineProgress Dataclass to Blackbox

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`

- [ ] **Step 1: Add PipelineProgress dataclass after BlackboxPipelineState**

Add this at the end of the file:

```python
@dataclass
class PipelineProgress:
    """工作流进度查询返回值。"""
    workflow_id: str
    elapsed_ms: int
    current_phase: str | None
    current_agent: str | None
    completed_agents: list[str]
    status: str
```

- [ ] **Step 2: Run Python syntax check**

Run: `python -m py_compile packages/blackbox/src/shannon_blackbox/pipeline/shared.py`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/shared.py
git commit -m "feat(blackbox): add PipelineProgress dataclass"
```

---

## Task 6: Add current_agent Field to Blackbox PipelineState

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`

- [ ] **Step 1: Add current_agent field to BlackboxPipelineState**

Find the `BlackboxPipelineState` dataclass and add the field:

```python
@dataclass
class BlackboxPipelineState:
    status: str = "running"
    current_phase: str | None = None
    current_agent: str | None = None
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    has_whitebox_results: bool = False
    found_whitebox_classes: list[str] = field(default_factory=list)
    start_time: float = 0.0
    errors: list[str] = field(default_factory=list)
    error_code: str | None = None
    failed_agents: list[str] = field(default_factory=list)
```

- [ ] **Step 2: Run Python syntax check**

Run: `python -m py_compile packages/blackbox/src/shannon_blackbox/pipeline/shared.py`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/shared.py
git commit -m "feat(blackbox): add current_agent to BlackboxPipelineState"
```

---

## Task 7: Add Query Handler to Blackbox Workflow

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

- [ ] **Step 1: Import PipelineProgress**

Add import at the top with the other imports from `.shared`:

```python
from .shared import BlackboxActivityInput, BlackboxPipelineInput, BlackboxPipelineState, PipelineProgress
```

- [ ] **Step 2: Add query handler method to BlackboxScanWorkflow**

Add this method inside the `BlackboxScanWorkflow` class (after the `run` method):

```python
@workflow.query
def pipeline_progress(self) -> PipelineProgress:
    """返回当前工作流进度供 CLI 轮询。"""
    from temporalio import workflow as wf

    elapsed_ns = wf.time_ns() - int(self._state.start_time * 1e9)

    return PipelineProgress(
        workflow_id=wf.info().workflow_id,
        elapsed_ms=elapsed_ns // 1_000_000,
        current_phase=self._state.current_phase,
        current_agent=self._state.current_agent,
        completed_agents=self._state.completed_agents,
        status=self._state.status,
    )
```

- [ ] **Step 3: Run Python syntax check**

Run: `python -m py_compile packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`
Expected: No output (success)

- [ ] **Step 4: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/workflows.py
git commit -m "feat(blackbox): add pipeline_progress query handler"
```

---

## Task 8: Update State Tracking in Blackbox Workflow

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

- [ ] **Step 1: Add recon phase tracking**

Find the RECON_BLACKBOX activity execution and add state updates:

```python
if not has_whitebox_results and AgentName.RECON_BLACKBOX.value not in self._state.completed_agents:
    self._state.current_phase = "recon-blackbox"
    self._state.current_agent = AgentName.RECON_BLACKBOX.value
    recon_input = BlackboxActivityInput(**{**act_input.__dict__})
    metrics = await workflow.execute_activity(
        activities.run_recon, recon_input,
        start_to_close_timeout=timedelta(hours=2),
        retry_policy=retry_policy,
    )
    self._state.completed_agents.append(AgentName.RECON_BLACKBOX.value)
    self._state.agent_metrics[AgentName.RECON_BLACKBOX.value] = metrics
    self._state.current_agent = None
```

- [ ] **Step 2: Add exploitation phase tracking**

Find the exploit agents loop and add state updates:

```python
if input.exploit:
    self._state.current_phase = "exploitation"
    self._state.current_agent = "pipelines"
    # ... existing validation and scheduling code ...
    for vt, agent_name, task in exploit_tasks:
        self._state.current_agent = agent_name.value
        # ... activity execution ...
```

- [ ] **Step 3: Add reporting phase tracking**

Find the assemble_report activity call and add state updates:

```python
self._state.current_phase = "reporting"
self._state.current_agent = "assemble-report"
await workflow.execute_activity(
    activities.assemble_report, act_input,
    start_to_close_timeout=timedelta(minutes=5),
)
self._state.current_agent = None
```

- [ ] **Step 4: Update report agent tracking**

Find the run_report_agent activity call:

```python
if AgentName.REPORT.value not in self._state.completed_agents:
    self._state.current_agent = AgentName.REPORT.value
    metrics = await workflow.execute_activity(
        activities.run_report_agent, act_input,
        start_to_close_timeout=timedelta(hours=1),
        retry_policy=retry_policy,
    )
    self._state.completed_agents.append(AgentName.REPORT.value)
    self._state.agent_metrics[AgentName.REPORT.value] = metrics
    self._state.current_agent = None
```

- [ ] **Step 5: Reset state at completion**

Before returning the state, add:

```python
if self._state.failed_agents:
    self._state.status = "failed"
    # ... existing code ...
else:
    self._state.status = "completed"
self._state.current_phase = None
return self._state
```

- [ ] **Step 6: Run Python syntax check**

Run: `python -m py_compile packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`
Expected: No output (success)

- [ ] **Step 7: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/workflows.py
git commit -m "feat(blackbox): update state tracking during workflow execution"
```

---

## Task 9: Run Existing Tests

**Files:**
- Test: `packages/whitebox/tests/test_worker_progress.py`

- [ ] **Step 1: Run whitebox worker progress tests**

Run: `pytest packages/whitebox/tests/test_worker_progress.py -v`
Expected: All tests pass

- [ ] **Step 2: Run all whitebox tests**

Run: `pytest packages/whitebox/tests/ -v`
Expected: All tests pass

- [ ] **Step 3: Run all blackbox tests**

Run: `pytest packages/blackbox/tests/ -v`
Expected: All tests pass

---

## Task 10: Manual Integration Test

- [ ] **Step 1: Start whitebox scan and observe progress**

Run: `shannon-whitebox start -r /Users/mango/project/vuln-range/NodeGoat`
Expected: See progress output every 30 seconds like `[30s] Phase: pre-recon | Agent: pre-recon | Completed: 0/13`

- [ ] **Step 2: Verify scan completes successfully**

Expected: Scan finishes with "White-box scan complete." message

- [ ] **Step 3: Check deliverables were created**

Run: `ls -la workspaces/*/deliverables/`
Expected: Deliverables directory contains output files

- [ ] **Step 4: Start blackbox scan and observe progress**

Run: `shannon-blackbox start --url http://localhost:3000 -r /path/to/repo`
Expected: See progress output every 30 seconds

---

## Self-Review Results

**1. Spec coverage:**
- ✅ PipelineProgress dataclass (Task 1, 5)
- ✅ State fields current_phase/current_agent (Task 2, 6)
- ✅ Query handler (Task 3, 7)
- ✅ State tracking updates (Task 4, 8)
- ✅ Testing (Task 9, 10)

**2. Placeholder scan:** None found - all code is explicit

**3. Type consistency:** All types, method names, and field names are consistent across tasks
