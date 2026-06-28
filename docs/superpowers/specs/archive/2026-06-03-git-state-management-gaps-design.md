# Git State Management Gap Remediation

**Date**: 2026-06-03
**Status**: Approved
**Scope**: Fix 5 critical gaps in the Python GitManager and AgentExecutor compared to the TypeScript original

---

## Background

The Python rewrite (`/root/shannon-py`) has a working `GitManager` and `AgentExecutor`, but comparison with the TypeScript original (`/root/shannon/apps/worker/src/services/git-manager.ts` and `agent-execution.ts`) revealed 5 critical gaps where the Python version is functionally inferior. This spec covers only those gaps — no refactoring of working code.

## Gap Summary

| # | Gap | File | Severity |
|---|-----|------|----------|
| 1 | `attempt_number` not passed to `create_checkpoint` | `agents/executor.py` | Critical |
| 2 | Lock granularity — reset/clean outside lock | `git_manager.py` | Critical |
| 3 | Rollback lacks error wrapping | `git_manager.py` | Critical |
| 4 | No error classification function | `models/errors.py` | Critical |
| 5 | No deliverable snapshot/restore on failure | `agents/executor.py` | Critical |

---

## Fix 1: Pass `attempt_number` to `create_checkpoint`

**Problem**: `AgentExecutor.execute()` calls `GitManager.create_checkpoint(deliverables, agent_name)` without the `attempt` parameter. The default is 1, so the workspace cleanup branch (`attempt > 1`) is never triggered on retries.

**Fix**:

- Add `attempt_number: int = 1` parameter to `AgentExecutor.execute()`.
- Pass it through: `await GitManager.create_checkpoint(deliverables, agent_name, attempt=attempt_number)`.

**Files changed**: `packages/core/src/shannon_core/agents/executor.py`

---

## Fix 2: Expand Lock Scope

**Problem**: In `rollback()` and `create_checkpoint()` (when `attempt > 1`), the `reset --hard` and `clean -fd` commands execute outside `_git_lock`. The TypeScript original serializes all git writes through the semaphore.

**Fix**:

- `rollback()`: move `reset` and `clean` inside the `async with _git_lock` block.
- `create_checkpoint()` when `attempt > 1`: move `reset` and `clean` inside the existing `async with _git_lock` block, before `add -A`.

**Principle**: All commands that modify `.git/index` or the working tree must run under `_git_lock`.

**Files changed**: `packages/core/src/shannon_core/git_manager.py`

---

## Fix 3: Wrap Rollback Errors

**Problem**: `rollback()` has no try-catch. Any git exception propagates uncaught. The TypeScript version wraps failures in `PentestError(GIT_ROLLBACK_FAILED)` and returns a result object — rollback is best-effort cleanup.

**Fix**:

- Add try-except inside `rollback()`.
- Catch `PentestError` and re-raise (already classified).
- Catch all other exceptions, log the error, and return `GitResult(success=False, error=str(exc))`.
- Do NOT raise — rollback failure should not halt the agent lifecycle.

**Files changed**: `packages/core/src/shannon_core/git_manager.py`

---

## Fix 4: Error Classification Function

**Problem**: The TypeScript version has `classifyErrorForTemporal()` that maps `ErrorCode` to `{type, retryable}` for workflow retry decisions. Python has no equivalent.

**Fix**:

Add `ErrorClassification` dataclass and `classify_error()` function to `models/errors.py`:

```
ErrorClassification(error_type: str, retryable: bool)

classify_error(error: PentestError) -> ErrorClassification
```

Mapping table (matches TS `classifyByErrorCode`):

| ErrorCode | Type | Retryable |
|-----------|------|-----------|
| SPENDING_CAP_REACHED, INSUFFICIENT_CREDITS, BILLING_ERROR | BillingError | True |
| API_RATE_LIMITED | RateLimitError | True |
| CONFIG_NOT_FOUND, CONFIG_VALIDATION_FAILED, CONFIG_PARSE_ERROR, PROMPT_LOAD_FAILED | ConfigurationError | False |
| GIT_CHECKPOINT_FAILED, GIT_ROLLBACK_FAILED | GitError | False |
| OUTPUT_VALIDATION_FAILED, DELIVERABLE_NOT_FOUND | OutputValidationError | True |
| AGENT_EXECUTION_FAILED | AgentExecutionError | (from error.retryable) |
| REPO_NOT_FOUND, AUTH_FAILED, AUTH_LOGIN_FAILED | ConfigurationError | False |
| (default) | UnknownError | (from error.retryable) |

**Placement**: `models/errors.py` — the function is a mapping over `ErrorCode` + `PentestError`, same cohesion unit.

**Files changed**: `packages/core/src/shannon_core/models/errors.py`

---

## Fix 5: Deliverable Snapshot/Restore

**Problem**: The TypeScript `failAgent` snapshots the deliverable file to a temp directory before rollback, then restores it after. This preserves the failed attempt's output for debugging/audit. Python's `AgentExecutor` skips this entirely — the file is lost after rollback.

**Fix**:

Add two static helpers to `AgentExecutor`:

- `_snapshot_deliverable(deliverables, agent_name) -> Path | None`: copies the deliverable file to a temp directory. Returns `None` if the file doesn't exist or snapshot fails.
- `_restore_deliverable(snap_dir, deliverables, agent_name)`: copies the file back and cleans up the temp directory.

Integrate in both failure paths (spending cap and execution failure):

```python
snap = self._snapshot_deliverable(deliverables, agent_name)
await GitManager.rollback(deliverables, reason)
if snap:
    self._restore_deliverable(snap, deliverables, agent_name)
```

**Design decisions**:
- Use `tempfile.mkdtemp` with agent-name prefix to avoid concurrent conflicts.
- Snapshot failure is non-blocking — returns `None`, skip restore.
- Auto-cleanup temp directory after restore.

**Files changed**: `packages/core/src/shannon_core/agents/executor.py`

---

## Testing Strategy

Each fix should have corresponding test updates:

| Fix | Test file | New/updated tests |
|-----|-----------|-------------------|
| 1 | `test_executor.py` (or integration) | Verify `attempt_number` passed through |
| 2 | `test_git_manager.py` | Verify reset/clean happen inside lock (concurrent test) |
| 3 | `test_git_manager.py` | Verify rollback returns `GitResult(success=False)` on error |
| 4 | `test_errors.py` (new) | Verify `classify_error` mapping for all ErrorCode values |
| 5 | `test_executor.py` | Verify snapshot created before rollback, restored after |

## Out of Scope

- ActivityLogger integration (architecture-level change)
- Commit message emoji prefixes (cosmetic)
- `hadChanges` field on GitResult (minor)
- Temporal workflow consumer of `classify_error` (separate task)
