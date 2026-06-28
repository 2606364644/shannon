# Pipeline Progress Query Design

**Date:** 2026-06-09
**Author:** Claude
**Status:** Approved

## Problem

The whitebox and blackbox scan commands show no progress output after the initial "Starting scan..." message. Users cannot see which phase/agent is currently running or how long the scan has been in progress.

### Root Cause

Both `whitebox/worker.py` and `blackbox/worker.py` contain a `poll_workflow_progress()` function that queries the workflow for progress:

```python
progress = await handle.query("PipelineProgress")
```

However, neither `WhiteboxScanWorkflow` nor `BlackboxScanWorkflow` implements a `@workflow.query` handler named "PipelineProgress". The query fails silently (caught by `except Exception: pass`), so no output appears.

## Solution

Add Temporal query handlers to both workflows to return real-time progress information. This follows the pattern established in the original TypeScript project.

## Changes

### 1. Add PipelineProgress Dataclass

**File:** `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`
**File:** `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`

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

### 2. Add State Fields

**File:** `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`
**File:** `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`

Add to `PipelineState`:
```python
current_phase: str | None = None
current_agent: str | None = None
```

### 3. Add Query Handler to Workflows

**File:** `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`
**File:** `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

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

### 4. Update State During Workflow Execution

Throughout the workflow, update `current_phase` and `current_agent` before and after each major activity:

```python
self._state.current_phase = "pre-recon"
self._state.current_agent = "pre-recon"
# ... execute activity ...
self._state.current_agent = None
```

## Expected Output

After this fix, users will see progress updates every 30 seconds:

```
Starting white-box scan on /path/to/repo
[30s] Phase: pre-recon | Agent: pre-recon | Completed: 0/13
[60s] Phase: recon | Agent: recon | Completed: 1/13
[90s] Phase: vulnerability-analysis | Agent: injection-vuln | Completed: 2/13
...
White-box scan complete.
```

## Files Modified

1. `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`
2. `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`
3. `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`
4. `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

## Testing

- Existing test `test_poll_workflow_progress_queries_and_prints` already expects this structure
- After implementation, run `shannon-whitebox start` and verify progress output appears
- Run `shannon-blackbox start` and verify progress output appears
