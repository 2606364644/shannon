# Git State Management Gap Remediation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Fix 5 critical gaps where the Python GitManager/AgentExecutor is functionally inferior to the TypeScript original.

**Architecture:** Minimal targeted fixes to existing files — no restructuring. Each fix is independent and verifiable.

**Tech Stack:** Python 3.12, asyncio, pytest + pytest-asyncio (`asyncio_mode = "auto"`)

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `packages/core/src/shannon_core/git_manager.py` | Fixes 2 & 3 (lock scope, rollback error wrap) |
| Modify | `packages/core/src/shannon_core/agents/executor.py` | Fixes 1 & 5 (attempt param, snapshot/restore) |
| Modify | `packages/core/src/shannon_core/models/errors.py` | Fix 4 (error classification) |
| Modify | `packages/core/tests/test_git_manager.py` | Tests for fixes 2 & 3 |
| Modify | `packages/core/tests/test_errors.py` | Tests for fix 4 |
| Create | `packages/core/tests/test_executor.py` | Tests for fixes 1 & 5 |

---

### Task 1: Add error classification function (`models/errors.py`)

**Files:**
- Modify: `packages/core/src/shannon_core/models/errors.py`

- [x] **Step 1: Write the failing tests**

Add to `packages/core/tests/test_errors.py`:

```python
from shannon_core.models.errors import (
    ErrorCode,
    ErrorClassification,
    PentestError,
    classify_error,
)


class TestErrorClassification:
    """Tests for classify_error mapping (Fix 4)."""

    def test_billing_errors_are_retryable(self):
        for code in (ErrorCode.SPENDING_CAP_REACHED, ErrorCode.INSUFFICIENT_CREDITS, ErrorCode.BILLING_ERROR):
            err = PentestError("msg", "billing", error_code=code)
            result = classify_error(err)
            assert result.error_type == "BillingError", f"{code} should be BillingError"
            assert result.retryable is True, f"{code} should be retryable"

    def test_rate_limit_is_retryable(self):
        err = PentestError("msg", "rate", error_code=ErrorCode.API_RATE_LIMITED)
        result = classify_error(err)
        assert result.error_type == "RateLimitError"
        assert result.retryable is True

    def test_config_errors_are_not_retryable(self):
        for code in (ErrorCode.CONFIG_NOT_FOUND, ErrorCode.CONFIG_VALIDATION_FAILED,
                     ErrorCode.CONFIG_PARSE_ERROR, ErrorCode.PROMPT_LOAD_FAILED):
            err = PentestError("msg", "config", error_code=code)
            result = classify_error(err)
            assert result.error_type == "ConfigurationError", f"{code} should be ConfigurationError"
            assert result.retryable is False, f"{code} should not be retryable"

    def test_git_errors_are_not_retryable(self):
        for code in (ErrorCode.GIT_CHECKPOINT_FAILED, ErrorCode.GIT_ROLLBACK_FAILED):
            err = PentestError("msg", "infrastructure", error_code=code)
            result = classify_error(err)
            assert result.error_type == "GitError", f"{code} should be GitError"
            assert result.retryable is False

    def test_validation_errors_are_retryable(self):
        for code in (ErrorCode.OUTPUT_VALIDATION_FAILED, ErrorCode.DELIVERABLE_NOT_FOUND):
            err = PentestError("msg", "validation", error_code=code)
            result = classify_error(err)
            assert result.error_type == "OutputValidationError"
            assert result.retryable is True

    def test_agent_execution_uses_error_retryable_flag(self):
        err = PentestError("msg", "execution", retryable=True, error_code=ErrorCode.AGENT_EXECUTION_FAILED)
        result = classify_error(err)
        assert result.error_type == "AgentExecutionError"
        assert result.retryable is True

        err2 = PentestError("msg", "execution", retryable=False, error_code=ErrorCode.AGENT_EXECUTION_FAILED)
        result2 = classify_error(err2)
        assert result2.retryable is False

    def test_auth_errors_are_not_retryable(self):
        for code in (ErrorCode.REPO_NOT_FOUND, ErrorCode.AUTH_FAILED, ErrorCode.AUTH_LOGIN_FAILED):
            err = PentestError("msg", "auth", error_code=code)
            result = classify_error(err)
            assert result.error_type == "ConfigurationError"
            assert result.retryable is False

    def test_unknown_code_falls_back_to_error_retryable(self):
        err = PentestError("msg", "unknown", retryable=True, error_code=None)
        result = classify_error(err)
        assert result.error_type == "UnknownError"
        assert result.retryable is True

    def test_target_unreachable_is_unknown(self):
        err = PentestError("msg", "network", error_code=ErrorCode.TARGET_UNREACHABLE)
        result = classify_error(err)
        assert result.error_type == "UnknownError"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_errors.py::TestErrorClassification -v`
Expected: FAIL — `ImportError: cannot import name 'ErrorClassification' from 'shannon_core.models.errors'`

- [x] **Step 3: Write the implementation**

Add to the end of `packages/core/src/shannon_core/models/errors.py`:

```python
class ErrorClassification:
    """Classification of a PentestError for workflow retry decisions."""

    __slots__ = ("error_type", "retryable")

    def __init__(self, error_type: str, retryable: bool) -> None:
        self.error_type = error_type
        self.retryable = retryable

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ErrorClassification):
            return NotImplemented
        return self.error_type == other.error_type and self.retryable == other.retryable

    def __repr__(self) -> str:
        return f"ErrorClassification(error_type={self.error_type!r}, retryable={self.retryable!r})"


def classify_error(error: PentestError) -> ErrorClassification:
    """Classify a PentestError for workflow retry behavior.

    Maps ErrorCode values to {error_type, retryable} pairs matching
    the TypeScript classifyByErrorCode function.
    """
    code = error.error_code

    if code in (ErrorCode.SPENDING_CAP_REACHED, ErrorCode.INSUFFICIENT_CREDITS, ErrorCode.BILLING_ERROR):
        return ErrorClassification("BillingError", True)

    if code == ErrorCode.API_RATE_LIMITED:
        return ErrorClassification("RateLimitError", True)

    if code in (ErrorCode.CONFIG_NOT_FOUND, ErrorCode.CONFIG_VALIDATION_FAILED,
                ErrorCode.CONFIG_PARSE_ERROR, ErrorCode.PROMPT_LOAD_FAILED):
        return ErrorClassification("ConfigurationError", False)

    if code in (ErrorCode.GIT_CHECKPOINT_FAILED, ErrorCode.GIT_ROLLBACK_FAILED):
        return ErrorClassification("GitError", False)

    if code in (ErrorCode.OUTPUT_VALIDATION_FAILED, ErrorCode.DELIVERABLE_NOT_FOUND):
        return ErrorClassification("OutputValidationError", True)

    if code == ErrorCode.AGENT_EXECUTION_FAILED:
        return ErrorClassification("AgentExecutionError", error.retryable)

    if code in (ErrorCode.REPO_NOT_FOUND, ErrorCode.AUTH_FAILED, ErrorCode.AUTH_LOGIN_FAILED):
        return ErrorClassification("ConfigurationError", False)

    return ErrorClassification("UnknownError", error.retryable)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_errors.py -v`
Expected: All tests PASS (both existing and new).

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/models/errors.py packages/core/tests/test_errors.py
git commit -m "feat(core): add ErrorClassification and classify_error for workflow retry decisions"
```

---

### Task 2: Expand lock scope in GitManager (`git_manager.py`)

**Files:**
- Modify: `packages/core/src/shannon_core/git_manager.py:83-105` (create_checkpoint — move reset/clean into lock)
- Modify: `packages/core/src/shannon_core/git_manager.py:139-152` (rollback — move reset/clean into lock)
- Modify: `packages/core/tests/test_git_manager.py`

- [x] **Step 1: Write the failing test**

Add to `packages/core/tests/test_git_manager.py`:

```python
async def test_rollback_holds_lock_during_reset_and_clean(git_repo: Path, monkeypatch):
    """Verify reset and clean run inside the lock, not outside."""
    (git_repo / "dirty.txt").write_text("dirty")
    subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True, check=True)

    lock_held_during: list[bool] = []
    original_run_git = GitManager._run_git

    async def tracking_run_git(repo_path: Path, *args: str):
        # Record whether the lock is held when reset/clean are called
        if args and args[0] in ("reset", "clean"):
            lock_held_during.append(GitManager._git_lock.locked())
        return await original_run_git(repo_path, *args)

    monkeypatch.setattr(GitManager, "_run_git", staticmethod(tracking_run_git))
    GitManager._git_lock = asyncio.Lock()

    await GitManager.rollback(git_repo, "lock test")

    assert len(lock_held_during) >= 2, "Expected at least reset + clean calls"
    assert all(lock_held_during), (
        f"reset/clean should run inside lock, but got: {lock_held_during}"
    )
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_git_manager.py::test_rollback_holds_lock_during_reset_and_clean -v`
Expected: FAIL — `AssertionError` because `lock_held_during` contains `False` entries.

- [x] **Step 3: Fix `rollback()` lock scope**

In `packages/core/src/shannon_core/git_manager.py`, replace the `rollback` method (lines 138–152) with:

```python
    @staticmethod
    async def rollback(repo_path: Path, reason: str) -> GitResult:
        """Hard-reset to HEAD and remove untracked files."""
        if not await GitManager.is_git_repository(repo_path):
            logger.warning("Rollback skipped: not a git repository (%s)", repo_path)
            return GitResult(success=True)

        async with GitManager._git_lock:
            changed = await GitManager._get_changed_files(repo_path)
            await GitManager._run_git(repo_path, "reset", "--hard", "HEAD")
            await GitManager._run_git(repo_path, "clean", "-fd")

        _log_change_summary(changed, f"Rollback ({reason})")
        logger.info("Rollback completed: %s", reason)
        return GitResult(success=True, changed_files=changed)
```

- [x] **Step 4: Fix `create_checkpoint()` lock scope**

In `packages/core/src/shannon_core/git_manager.py`, replace the `create_checkpoint` method (lines 67–105) with:

```python
    @staticmethod
    async def create_checkpoint(
        repo_path: Path,
        agent_name: str | AgentName,
        attempt: int = 1,
    ) -> GitResult:
        """Create a git checkpoint before agent execution.

        attempt == 1  → preserve existing changes, then add + commit
        attempt >  1  → reset + clean first, then add + commit
        """
        if not await GitManager.is_git_repository(repo_path):
            logger.warning("Checkpoint skipped: not a git repository (%s)", repo_path)
            return GitResult(success=True)

        name = agent_name.value if isinstance(agent_name, AgentName) else agent_name

        async with GitManager._git_lock:
            # On retries, clean workspace first
            if attempt > 1:
                await GitManager._run_git(repo_path, "reset", "--hard", "HEAD")
                await GitManager._run_git(repo_path, "clean", "-fd")

            await GitManager._run_git(repo_path, "add", "-A")
            changed = await GitManager._get_changed_files(repo_path)
            msg = f"checkpoint: before {name} (attempt {attempt})"
            result = await GitManager._run_git_with_retry(
                repo_path, "commit", "--allow-empty", "-m", msg,
            )

        if result.returncode != 0:
            raise PentestError(
                f"Git checkpoint failed for {name}: {result.stderr}",
                "infrastructure",
                error_code=ErrorCode.GIT_CHECKPOINT_FAILED,
                context={"agent": name, "attempt": attempt},
            )

        _log_change_summary(changed, f"Checkpoint ({name})")
        return GitResult(success=True, changed_files=changed)
```

- [x] **Step 5: Run all git manager tests**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_git_manager.py -v`
Expected: All tests PASS (including new lock test and all existing tests).

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/git_manager.py packages/core/tests/test_git_manager.py
git commit -m "fix(core): expand GitManager lock scope to cover reset/clean operations"
```

---

### Task 3: Add rollback error wrapping (`git_manager.py`)

**Files:**
- Modify: `packages/core/src/shannon_core/git_manager.py` (rollback method from Task 2)
- Modify: `packages/core/tests/test_git_manager.py`

- [x] **Step 1: Write the failing test**

Add to `packages/core/tests/test_git_manager.py`:

```python
async def test_rollback_returns_error_on_git_failure(git_repo: Path, monkeypatch):
    """When git commands fail, rollback returns GitResult(success=False) instead of raising."""
    async def failing_run_git(repo_path: Path, *args: str):
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository",
        )

    monkeypatch.setattr(GitManager, "_run_git", staticmethod(failing_run_git))
    GitManager._git_lock = asyncio.Lock()

    result = await GitManager.rollback(git_repo, "simulated failure")
    assert result.success is False
    assert result.error is not None
    assert "fatal" in result.error


async def test_rollback_reraises_pentest_error(git_repo: Path, monkeypatch):
    """When rollback hits a PentestError (e.g. git not found), it re-raises."""
    async def pentest_failing_run_git(repo_path: Path, *args: str):
        raise PentestError(
            "git not found in PATH",
            "infrastructure",
            error_code=ErrorCode.GIT_CHECKPOINT_FAILED,
        )

    monkeypatch.setattr(GitManager, "_run_git", staticmethod(pentest_failing_run_git))
    GitManager._git_lock = asyncio.Lock()

    with pytest.raises(PentestError) as exc_info:
        await GitManager.rollback(git_repo, "pentest error")
    assert "git not found" in str(exc_info.value)
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_git_manager.py::test_rollback_returns_error_on_git_failure packages/core/tests/test_git_manager.py::test_rollback_reraises_pentest_error -v`
Expected: FAIL — `test_rollback_returns_error_on_git_failure` raises an unhandled exception instead of returning a result.

- [x] **Step 3: Add try-except to rollback**

Replace the `rollback` method in `packages/core/src/shannon_core/git_manager.py` with:

```python
    @staticmethod
    async def rollback(repo_path: Path, reason: str) -> GitResult:
        """Hard-reset to HEAD and remove untracked files.

        Rollback is best-effort: returns ``GitResult(success=False)`` on
        failure instead of raising, so callers can continue cleanup.
        PentestError (e.g. git not found) is re-raised since it indicates
        a fundamental infrastructure problem.
        """
        if not await GitManager.is_git_repository(repo_path):
            logger.warning("Rollback skipped: not a git repository (%s)", repo_path)
            return GitResult(success=True)

        try:
            async with GitManager._git_lock:
                changed = await GitManager._get_changed_files(repo_path)
                await GitManager._run_git(repo_path, "reset", "--hard", "HEAD")
                await GitManager._run_git(repo_path, "clean", "-fd")

            _log_change_summary(changed, f"Rollback ({reason})")
            logger.info("Rollback completed: %s", reason)
            return GitResult(success=True, changed_files=changed)
        except PentestError:
            raise
        except Exception as exc:
            logger.error("Rollback failed: %s", exc)
            return GitResult(success=False, error=str(exc))
```

- [x] **Step 4: Run all git manager tests**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_git_manager.py -v`
Expected: All tests PASS.

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/git_manager.py packages/core/tests/test_git_manager.py
git commit -m "fix(core): wrap rollback errors, return GitResult instead of raising"
```

---

### Task 4: Pass `attempt_number` through `AgentExecutor.execute()` (`executor.py`)

**Files:**
- Modify: `packages/core/src/shannon_core/agents/executor.py:21-33` (execute signature)
- Modify: `packages/core/src/shannon_core/agents/executor.py:55` (create_checkpoint call)
- Create: `packages/core/tests/test_executor.py`

- [x] **Step 1: Write the failing test**

Create `packages/core/tests/test_executor.py`:

```python
"""Tests for AgentExecutor — attempt_number passthrough and snapshot/restore."""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from shannon_core.agents.executor import AgentExecutor
from shannon_core.models.agents import AgentName


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repository with one initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, capture_output=True, check=True,
    )
    (repo / "initial.txt").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo, capture_output=True, check=True,
    )
    return repo


class TestAttemptNumberPassthrough:
    """Verify attempt_number is forwarded to GitManager.create_checkpoint."""

    async def test_default_attempt_is_1(self, git_repo: Path):
        """When no attempt_number is given, create_checkpoint should receive attempt=1."""
        with patch("shannon_core.agents.executor.GitManager") as mock_gm:
            mock_gm.create_checkpoint = AsyncMock(return_value=None)
            mock_gm.rollback = AsyncMock(return_value=None)
            mock_gm.commit = AsyncMock(return_value=None)
            # We only care about the call to create_checkpoint, not the full execution
            executor = AgentExecutor(prompt_manager=None)

            # Patch run_claude_prompt to avoid actual AI calls
            with patch("shannon_core.agents.executor.run_claude_prompt") as mock_run:
                from shannon_core.agents.runner import ClaudeRunResult, TokenUsage
                mock_run.return_value = ClaudeRunResult(
                    success=True, text="ok", error=None,
                    turns=3, cost=0.01, model="test",
                    tokens=TokenUsage(input_tokens=10, output_tokens=20,
                                      cache_read_input_tokens=0, cache_creation_input_tokens=0),
                    structured_output=None, retryable=True,
                )
                with patch("shannon_core.agents.executor.validate_deliverable"):
                    await executor.execute(
                        agent_name=AgentName.PRE_RECON,
                        repo_path=str(git_repo),
                    )

            mock_gm.create_checkpoint.assert_called_once()
            call_kwargs = mock_gm.create_checkpoint.call_args
            # Third positional arg or 'attempt' kwarg should be 1
            if call_kwargs.kwargs.get("attempt") is not None:
                assert call_kwargs.kwargs["attempt"] == 1
            else:
                assert call_kwargs.args[2] == 1

    async def test_attempt_number_forwarded(self, git_repo: Path):
        """When attempt_number=3 is given, create_checkpoint should receive attempt=3."""
        with patch("shannon_core.agents.executor.GitManager") as mock_gm:
            mock_gm.create_checkpoint = AsyncMock(return_value=None)
            mock_gm.rollback = AsyncMock(return_value=None)
            mock_gm.commit = AsyncMock(return_value=None)

            executor = AgentExecutor(prompt_manager=None)

            with patch("shannon_core.agents.executor.run_claude_prompt") as mock_run:
                from shannon_core.agents.runner import ClaudeRunResult, TokenUsage
                mock_run.return_value = ClaudeRunResult(
                    success=True, text="ok", error=None,
                    turns=3, cost=0.01, model="test",
                    tokens=TokenUsage(input_tokens=10, output_tokens=20,
                                      cache_read_input_tokens=0, cache_creation_input_tokens=0),
                    structured_output=None, retryable=True,
                )
                with patch("shannon_core.agents.executor.validate_deliverable"):
                    await executor.execute(
                        agent_name=AgentName.PRE_RECON,
                        repo_path=str(git_repo),
                        attempt_number=3,
                    )

            mock_gm.create_checkpoint.assert_called_once()
            call_kwargs = mock_gm.create_checkpoint.call_args
            if "attempt" in call_kwargs.kwargs:
                assert call_kwargs.kwargs["attempt"] == 3
            else:
                assert call_kwargs.args[2] == 3
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_executor.py::TestAttemptNumberPassthrough -v`
Expected: FAIL — `TypeError: execute() got an unexpected keyword argument 'attempt_number'`

- [x] **Step 3: Add `attempt_number` parameter to `execute()`**

In `packages/core/src/shannon_core/agents/executor.py`, modify the `execute` method signature and the `create_checkpoint` call.

Change line 21–33 from:

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
    ) -> AgentMetrics:
```

to:

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
        attempt_number: int = 1,
    ) -> AgentMetrics:
```

Change line 55 from:

```python
        await GitManager.create_checkpoint(deliverables, agent_name)
```

to:

```python
        await GitManager.create_checkpoint(deliverables, agent_name, attempt=attempt_number)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_executor.py::TestAttemptNumberPassthrough -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/executor.py packages/core/tests/test_executor.py
git commit -m "feat(core): pass attempt_number through AgentExecutor to GitManager"
```

---

### Task 5: Add deliverable snapshot/restore on failure (`executor.py`)

**Files:**
- Modify: `packages/core/src/shannon_core/agents/executor.py`
- Modify: `packages/core/tests/test_executor.py`

- [x] **Step 1: Write the failing tests**

Add to `packages/core/tests/test_executor.py`:

```python
class TestDeliverableSnapshotRestore:
    """Verify deliverable files are snapshotted before rollback and restored after."""

    async def test_snapshot_preserves_deliverable_on_spending_cap(self, git_repo: Path):
        """When spending cap triggers rollback, deliverable should survive."""
        from shannon_core.models.agents import AGENTS
        from shannon_core.models.result import GitResult

        defn = AGENTS[AgentName.PRE_RECON]
        filename = defn.deliverable_filename
        assert filename is not None

        executor = AgentExecutor(prompt_manager=None)

        with patch("shannon_core.agents.executor.GitManager") as mock_gm:
            mock_gm.create_checkpoint = AsyncMock(return_value=GitResult(success=True))
            mock_gm.rollback = AsyncMock(return_value=GitResult(success=True))
            mock_gm.commit = AsyncMock(return_value=GitResult(success=True))

            with patch("shannon_core.agents.executor.run_claude_prompt") as mock_run:
                from shannon_core.agents.runner import ClaudeRunResult, TokenUsage
                # Simulate spending cap: success=True but low turns/cost
                mock_run.return_value = ClaudeRunResult(
                    success=True, text="spending cap reached", error=None,
                    turns=1, cost=0, model="test",
                    tokens=TokenUsage(input_tokens=10, output_tokens=20,
                                      cache_read_input_tokens=0, cache_creation_input_tokens=0),
                    structured_output=None, retryable=True,
                )
                with patch("shannon_core.agents.executor.is_spending_cap_behavior", return_value=True):
                    deliverables = git_repo / ".shannon" / "deliverables"
                    deliverables.mkdir(parents=True, exist_ok=True)
                    (deliverables / filename).write_text("# Important deliverable content")

                    with pytest.raises(Exception):
                        await executor.execute(
                            agent_name=AgentName.PRE_RECON,
                            repo_path=str(git_repo),
                            deliverables_path=str(deliverables),
                        )

                    # Deliverable should still exist after rollback
                    assert (deliverables / filename).exists(), \
                        f"Deliverable {filename} should be restored after rollback"
                    assert (deliverables / filename).read_text() == "# Important deliverable content"

    async def test_no_snapshot_when_no_deliverable(self, git_repo: Path):
        """When no deliverable file exists, snapshot should be skipped gracefully."""
        executor = AgentExecutor(prompt_manager=None)

        with patch("shannon_core.agents.executor.GitManager") as mock_gm:
            mock_gm.create_checkpoint = AsyncMock(return_value=GitResult(success=True))
            mock_gm.rollback = AsyncMock(return_value=GitResult(success=True))

            with patch("shannon_core.agents.executor.run_claude_prompt") as mock_run:
                from shannon_core.agents.runner import ClaudeRunResult, TokenUsage
                mock_run.return_value = ClaudeRunResult(
                    success=False, text="", error="execution failed",
                    turns=1, cost=0.01, model="test",
                    tokens=TokenUsage(input_tokens=10, output_tokens=20,
                                      cache_read_input_tokens=0, cache_creation_input_tokens=0),
                    structured_output=None, retryable=True,
                )

                with pytest.raises(Exception):
                    await executor.execute(
                        agent_name=AgentName.PRE_RECON,
                        repo_path=str(git_repo),
                    )

                # Should not crash — snapshot skipped gracefully
                mock_gm.rollback.assert_called_once()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_executor.py::TestDeliverableSnapshotRestore -v`
Expected: FAIL — deliverable file is not restored after rollback (or method lacks snapshot logic).

- [x] **Step 3: Add snapshot/restore helpers and integrate into failure paths**

Add two new imports at the top of `packages/core/src/shannon_core/agents/executor.py`:

```python
import shutil
import tempfile
```

Add the snapshot and restore methods to the `AgentExecutor` class (after `__init__`):

```python
    @staticmethod
    def _snapshot_deliverable(
        deliverables: Path,
        agent_name: AgentName,
    ) -> Path | None:
        """Snapshot deliverable file to a temp directory before rollback.

        Returns the snapshot directory path, or None if no file to snapshot.
        """
        defn = AGENTS[agent_name]
        if not defn.deliverable_filename:
            return None
        src = deliverables / defn.deliverable_filename
        if not src.exists():
            return None
        try:
            snap_dir = Path(tempfile.mkdtemp(prefix=f"shannon-snapshot-{agent_name.value}-"))
            shutil.copy2(src, snap_dir / defn.deliverable_filename)
            return snap_dir
        except Exception:
            return None

    @staticmethod
    def _restore_deliverable(
        snap_dir: Path,
        deliverables: Path,
        agent_name: AgentName,
    ) -> None:
        """Restore deliverable from snapshot and clean up temp directory."""
        defn = AGENTS[agent_name]
        if not defn.deliverable_filename:
            shutil.rmtree(snap_dir, ignore_errors=True)
            return
        src = snap_dir / defn.deliverable_filename
        try:
            if src.exists():
                deliverables.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, deliverables / defn.deliverable_filename)
        finally:
            shutil.rmtree(snap_dir, ignore_errors=True)
```

Now modify the two failure paths in `execute()`. Replace the spending cap block (lines ~68–75):

From:

```python
        if result.success and is_spending_cap_behavior(result.turns, result.cost, result.text):
            await GitManager.rollback(deliverables, "spending cap detected")
            raise PentestError(
                f"Spending cap likely reached (turns={result.turns}, cost=${result.cost})",
                "billing",
                retryable=True,
                error_code=ErrorCode.SPENDING_CAP_REACHED,
            )
```

To:

```python
        if result.success and is_spending_cap_behavior(result.turns, result.cost, result.text):
            snap = self._snapshot_deliverable(deliverables, agent_name)
            await GitManager.rollback(deliverables, "spending cap detected")
            if snap:
                self._restore_deliverable(snap, deliverables, agent_name)
            raise PentestError(
                f"Spending cap likely reached (turns={result.turns}, cost=${result.cost})",
                "billing",
                retryable=True,
                error_code=ErrorCode.SPENDING_CAP_REACHED,
            )
```

Replace the execution failure block (lines ~77–84):

From:

```python
        if not result.success:
            await GitManager.rollback(deliverables, "execution failure")
            raise PentestError(
                result.error or f"Agent {agent_name.value} execution failed",
                "validation",
                retryable=result.retryable,
                error_code=ErrorCode.AGENT_EXECUTION_FAILED,
            )
```

To:

```python
        if not result.success:
            snap = self._snapshot_deliverable(deliverables, agent_name)
            await GitManager.rollback(deliverables, "execution failure")
            if snap:
                self._restore_deliverable(snap, deliverables, agent_name)
            raise PentestError(
                result.error or f"Agent {agent_name.value} execution failed",
                "validation",
                retryable=result.retryable,
                error_code=ErrorCode.AGENT_EXECUTION_FAILED,
            )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_executor.py -v`
Expected: All tests PASS.

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/agents/executor.py packages/core/tests/test_executor.py
git commit -m "feat(core): snapshot and restore deliverables before rollback on failure"
```

---

### Task 6: Run full test suite and verify no regressions

**Files:** None (verification only)

- [x] **Step 1: Run all tests in the core package**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/ -v`
Expected: All tests PASS.

- [x] **Step 2: Run all tests across all packages**

Run: `cd /root/shannon-py && uv run pytest --timeout=120 -v`
Expected: All tests PASS.

- [x] **Step 3: Final commit (if any test fixes were needed)**

```bash
git add -A
git commit -m "test: fix any test regressions from git state management gap fixes"
```
