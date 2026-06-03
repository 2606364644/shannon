# GitManager Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fully align Python GitManager with the original TypeScript implementation — async rewrite with concurrency control, retry mechanism, change tracking, and logging.

**Architecture:** Replace the synchronous `subprocess.run`-based GitManager with an async implementation using `asyncio.create_subprocess_exec`. An `asyncio.Lock` serializes git operations within the process. Exponential-backoff retry handles external lock conflicts. A new `GitResult` dataclass returns structured results with changed-file tracking.

**Tech Stack:** Python 3.12, asyncio, pytest-asyncio (auto mode), unittest.mock, subprocess

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `packages/core/src/shannon_core/models/result.py` | Modify (append) | Add `GitResult` dataclass alongside existing `WhiteboxScanResult`/`BlackboxScanResult` |
| `packages/core/src/shannon_core/models/__init__.py` | Modify | Export `GitResult` |
| `packages/core/src/shannon_core/git_manager.py` | Rewrite | Full async GitManager with lock, retry, tracking |
| `packages/core/src/shannon_core/agents/executor.py` | Modify | 4 call sites: add `await`, pass `attempt` |
| `packages/core/tests/test_git_manager.py` | Rewrite | Async tests covering all 7 spec scenarios |

---

### Task 1: Add `GitResult` dataclass

**Files:**
- Modify: `packages/core/src/shannon_core/models/result.py`
- Modify: `packages/core/src/shannon_core/models/__init__.py`
- Test: `packages/core/tests/test_git_manager.py` (covered in Task 3+)

- [ ] **Step 1: Append `GitResult` to result.py**

Add at the end of `packages/core/src/shannon_core/models/result.py`:

```python
from dataclasses import dataclass, field


@dataclass
class GitResult:
    """Git 操作的统一返回类型。"""
    success: bool
    changed_files: list[str] = field(default_factory=list)
    error: str | None = None
```

Note: Keep the existing `from pydantic import BaseModel` import and the two Pydantic models (`WhiteboxScanResult`, `BlackboxScanResult`) untouched. Add `from dataclasses import dataclass, field` as a new import at the top.

- [ ] **Step 2: Export `GitResult` from models `__init__.py`**

In `packages/core/src/shannon_core/models/__init__.py`, add `GitResult` to the import line from `.result`:

```python
from .result import BlackboxScanResult, GitResult, WhiteboxScanResult
```

And add `"GitResult"` to the `__all__` list (alphabetically, between `"DELIVERABLE_FILENAMES"` and `"InjectionVulnerability"`).

- [ ] **Step 3: Verify import works**

Run: `cd /root/shannon-py && python -c "from shannon_core.models import GitResult; print(GitResult(success=True))"`
Expected: `GitResult(success=True, changed_files=[], error=None)`

- [ ] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/models/result.py packages/core/src/shannon_core/models/__init__.py
git commit -m "feat(core): add GitResult dataclass for structured git operation results"
```

---

### Task 2: Rewrite GitManager — async core + `_run_git` + lock

**Files:**
- Rewrite: `packages/core/src/shannon_core/git_manager.py`

- [ ] **Step 1: Write the full async GitManager implementation**

Replace the entire contents of `packages/core/src/shannon_core/git_manager.py` with:

```python
from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import ClassVar

from shannon_core.models.agents import AgentName
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.models.result import GitResult

logger = logging.getLogger(__name__)

_GIT_LOCK_PATTERNS: list[str] = [
    "index.lock",
    "unable to lock",
    "Another git process",
    "fatal: Unable to create",
    "fatal: index file",
]


def _is_git_lock_error(stderr: str) -> bool:
    return any(p in stderr for p in _GIT_LOCK_PATTERNS)


def _log_change_summary(
    changed_files: list[str],
    action: str,
    max_show: int = 5,
) -> None:
    if not changed_files:
        logger.info("%s: no file changes", action)
        return
    if len(changed_files) <= max_show:
        logger.info("%s: %s", action, ", ".join(changed_files))
    else:
        shown = ", ".join(changed_files[:max_show])
        logger.info(
            "%s: %s ... and %d more files",
            action,
            shown,
            len(changed_files) - max_show,
        )


class GitManager:
    """Async git operations with concurrency control and retry."""

    _git_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    async def is_git_repository(repo_path: Path) -> bool:
        """Check if *repo_path* is inside a git repository."""
        try:
            await GitManager._run_git(repo_path, "rev-parse", "--git-dir")
            return True
        except Exception:
            return False

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

        # On retries, clean workspace first
        if attempt > 1:
            await GitManager._run_git(repo_path, "reset", "--hard", "HEAD")
            await GitManager._run_git(repo_path, "clean", "-fd")

        async with GitManager._git_lock:
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

    @staticmethod
    async def commit(
        repo_path: Path,
        agent_name: str | AgentName,
    ) -> GitResult:
        """Commit agent deliverables."""
        if not await GitManager.is_git_repository(repo_path):
            logger.warning("Commit skipped: not a git repository (%s)", repo_path)
            return GitResult(success=True)

        name = agent_name.value if isinstance(agent_name, AgentName) else agent_name

        async with GitManager._git_lock:
            await GitManager._run_git(repo_path, "add", "-A")
            changed = await GitManager._get_changed_files(repo_path)
            msg = f"deliverable: {name}"
            result = await GitManager._run_git_with_retry(
                repo_path, "commit", "--allow-empty", "-m", msg,
            )

        if result.returncode != 0:
            raise PentestError(
                f"Git commit failed for {name}: {result.stderr}",
                "infrastructure",
                error_code=ErrorCode.GIT_CHECKPOINT_FAILED,
                context={"agent": name},
            )

        _log_change_summary(changed, f"Committed ({name})")
        return GitResult(success=True, changed_files=changed)

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

    @staticmethod
    async def get_commit_hash(repo_path: Path) -> str | None:
        """Return the current HEAD commit hash, or *None* on failure."""
        try:
            result = await GitManager._run_git(repo_path, "rev-parse", "HEAD")
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    @staticmethod
    async def execute_with_retry(
        repo_path: Path,
        *args: str,
        description: str = "",
        max_retries: int = 5,
    ) -> subprocess.CompletedProcess:
        """Execute an arbitrary git command with lock-conflict retry."""
        return await GitManager._run_git_with_retry(
            repo_path,
            *args,
            max_retries=max_retries,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
        """Execute a single git command via asyncio subprocess."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", *args,
                cwd=str(repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await proc.communicate()
            return subprocess.CompletedProcess(
                args=["git", *args],
                returncode=proc.returncode or 0,
                stdout=stdout_bytes.decode().strip(),
                stderr=stderr_bytes.decode().strip(),
            )
        except FileNotFoundError:
            raise PentestError(
                "git not found in PATH",
                "infrastructure",
                error_code=ErrorCode.GIT_CHECKPOINT_FAILED,
            )

    @staticmethod
    async def _run_git_with_retry(
        repo_path: Path,
        *args: str,
        max_retries: int = 5,
    ) -> subprocess.CompletedProcess:
        """Run git with exponential backoff on lock errors."""
        result = await GitManager._run_git(repo_path, *args)
        for attempt in range(max_retries):
            if result.returncode == 0:
                return result
            if not _is_git_lock_error(result.stderr):
                return result
            delay = 2 ** attempt * 0.5
            logger.warning(
                "Git lock conflict on '%s', retrying in %.1fs (attempt %d/%d)",
                " ".join(args), delay, attempt + 1, max_retries,
            )
            await asyncio.sleep(delay)
            result = await GitManager._run_git(repo_path, *args)
        return result

    @staticmethod
    async def _get_changed_files(repo_path: Path) -> list[str]:
        """Return a list of changed file entries from ``git status --porcelain``."""
        result = await GitManager._run_git(repo_path, "status", "--porcelain")
        if result.returncode != 0:
            return []
        lines = result.stdout.strip().split("\n")
        return [line.strip() for line in lines if line.strip()]
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `cd /root/shannon-py && python -c "from shannon_core.git_manager import GitManager; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add packages/core/src/shannon_core/git_manager.py
git commit -m "feat(core): rewrite GitManager as async with lock, retry, and change tracking"
```

---

### Task 3: Write tests — basic async functions

**Files:**
- Rewrite: `packages/core/tests/test_git_manager.py`

- [ ] **Step 1: Write async test fixture and basic tests**

Replace the entire contents of `packages/core/tests/test_git_manager.py` with:

```python
import asyncio
import subprocess

import pytest
from pathlib import Path

from shannon_core.git_manager import GitManager
from shannon_core.models.errors import PentestError
from shannon_core.models.result import GitResult


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


# ---- is_git_repository ----


async def test_is_git_repository_true(git_repo: Path):
    assert await GitManager.is_git_repository(git_repo) is True


async def test_is_git_repository_false(tmp_path: Path):
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()
    assert await GitManager.is_git_repository(non_repo) is False


# ---- get_commit_hash ----


async def test_get_commit_hash(git_repo: Path):
    h = await GitManager.get_commit_hash(git_repo)
    assert h is not None
    assert len(h) == 40


async def test_get_commit_hash_non_repo(tmp_path: Path):
    h = await GitManager.get_commit_hash(tmp_path / "nonexistent")
    assert h is None


# ---- create_checkpoint ----


async def test_create_checkpoint_attempt1(git_repo: Path):
    result = await GitManager.create_checkpoint(git_repo, "pre-recon", attempt=1)
    assert result.success is True


async def test_create_checkpoint_attempt1_preserves_files(git_repo: Path):
    """On first attempt, existing uncommitted files should be preserved in the checkpoint."""
    (git_repo / "existing.txt").write_text("kept")
    await GitManager.create_checkpoint(git_repo, "pre-recon", attempt=1)
    assert (git_repo / "existing.txt").exists()
    # The file should be committed in the checkpoint
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=git_repo, capture_output=True, text=True, check=True,
    )
    assert "checkpoint" in log.stdout


async def test_create_checkpoint_retry_cleans_first(git_repo: Path):
    """On attempt > 1, workspace should be cleaned before checkpoint."""
    (git_repo / "stale.txt").write_text("stale")
    subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True, check=True)

    result = await GitManager.create_checkpoint(git_repo, "pre-recon", attempt=2)
    assert result.success is True
    assert not (git_repo / "stale.txt").exists()


async def test_create_checkpoint_non_git_dir(tmp_path: Path):
    non_repo = tmp_path / "not-git"
    non_repo.mkdir()
    result = await GitManager.create_checkpoint(non_repo, "agent")
    assert result.success is True
    assert result.changed_files == []


# ---- commit ----


async def test_commit_success(git_repo: Path):
    (git_repo / "deliverable.md").write_text("# Report")
    result = await GitManager.commit(git_repo, "pre-recon")
    assert result.success is True
    assert len(result.changed_files) > 0
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=git_repo, capture_output=True, text=True, check=True,
    )
    assert "deliverable: pre-recon" in log.stdout


async def test_commit_non_git_dir(tmp_path: Path):
    non_repo = tmp_path / "not-git"
    non_repo.mkdir()
    result = await GitManager.commit(non_repo, "agent")
    assert result.success is True


# ---- rollback ----


async def test_rollback_removes_files(git_repo: Path):
    (git_repo / "bad_file.txt").write_text("bad content")
    subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True, check=True)
    result = await GitManager.rollback(git_repo, "test failure")
    assert result.success is True
    assert not (git_repo / "bad_file.txt").exists()


async def test_rollback_removes_untracked(git_repo: Path):
    (git_repo / "untracked.txt").write_text("not tracked")
    result = await GitManager.rollback(git_repo, "cleanup")
    assert result.success is True
    assert not (git_repo / "untracked.txt").exists()


async def test_rollback_non_git_dir(tmp_path: Path):
    non_repo = tmp_path / "not-git"
    non_repo.mkdir()
    result = await GitManager.rollback(non_repo, "nothing to do")
    assert result.success is True


async def test_rollback_returns_changed_files(git_repo: Path):
    (git_repo / "file1.txt").write_text("a")
    (git_repo / "file2.txt").write_text("b")
    subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True, check=True)
    result = await GitManager.rollback(git_repo, "multi-file")
    assert result.success is True
    assert len(result.changed_files) >= 2
```

- [ ] **Step 2: Run the tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_git_manager.py -v`
Expected: All tests PASS (approximately 15 tests)

- [ ] **Step 3: Commit**

```bash
git add packages/core/tests/test_git_manager.py
git commit -m "test(core): async tests for basic GitManager operations"
```

---

### Task 4: Write tests — concurrency, retry, error handling

**Files:**
- Modify: `packages/core/tests/test_git_manager.py` (append)

- [ ] **Step 1: Append concurrency, retry, and error tests**

Append the following to `packages/core/tests/test_git_manager.py`:

```python
# ---- Concurrency ----


async def test_concurrent_checkpoints_serialized(git_repo: Path):
    """Multiple concurrent create_checkpoint calls should not raise lock errors."""
    (git_repo / "concurrent.txt").write_text("data")

    results = await asyncio.gather(
        GitManager.create_checkpoint(git_repo, "agent-a", attempt=1),
        GitManager.create_checkpoint(git_repo, "agent-b", attempt=1),
        GitManager.create_checkpoint(git_repo, "agent-c", attempt=1),
    )
    assert all(r.success for r in results)


# ---- Retry mechanism ----


async def test_retry_on_lock_error(git_repo: Path, monkeypatch):
    """Verify retry is attempted when a lock error occurs."""
    call_count = 0
    original_run_git = GitManager._run_git

    async def mock_run_git(repo_path: Path, *args: str):
        nonlocal call_count
        result = await original_run_git(repo_path, *args)
        # Inject a lock error on the first commit call
        if args and args[0] == "commit":
            call_count += 1
            if call_count == 1:
                result = subprocess.CompletedProcess(
                    args=["git", *args],
                    returncode=1,
                    stdout="",
                    stderr="fatal: Unable to create 'index.lock': File exists.",
                )
        return result

    monkeypatch.setattr(GitManager, "_run_git", staticmethod(mock_run_git))
    # Also need to mock _run_git_with_retry to use the patched _run_git
    # Instead, test via the retry path directly
    # Reset lock for clean state
    GitManager._git_lock = asyncio.Lock()

    result = await GitManager.create_checkpoint(git_repo, "retry-agent")
    assert result.success is True
    assert call_count >= 2  # Initial call + at least 1 retry


# ---- Error handling ----


async def test_create_checkpoint_raises_on_persistent_failure(git_repo: Path, monkeypatch):
    """When git commit keeps failing with non-lock errors, PentestError is raised."""
    async def mock_run_git_with_retry(repo_path: Path, *args: str, max_retries: int = 5):
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=1,
            stdout="",
            stderr="some non-lock error",
        )

    monkeypatch.setattr(GitManager, "_run_git_with_retry", staticmethod(mock_run_git_with_retry))

    with pytest.raises(PentestError) as exc_info:
        await GitManager.create_checkpoint(git_repo, "failing-agent")

    assert exc_info.value.error_code is not None
    assert "GIT_CHECKPOINT_FAILED" in str(exc_info.value.error_code)


async def test_commit_raises_on_failure(git_repo: Path, monkeypatch):
    async def mock_run_git_with_retry(repo_path: Path, *args: str, max_retries: int = 5):
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=1,
            stdout="",
            stderr="commit error",
        )

    monkeypatch.setattr(GitManager, "_run_git_with_retry", staticmethod(mock_run_git_with_retry))

    with pytest.raises(PentestError) as exc_info:
        await GitManager.commit(git_repo, "failing-agent")

    assert "GIT_CHECKPOINT_FAILED" in str(exc_info.value.error_code)


# ---- execute_with_retry ----


async def test_execute_with_retry_success(git_repo: Path):
    result = await GitManager.execute_with_retry(git_repo, "status", "--porcelain")
    assert result.returncode == 0


# ---- Change tracking ----


async def test_changed_files_tracked_on_commit(git_repo: Path):
    (git_repo / "new_file.py").write_text("print('hello')")
    result = await GitManager.commit(git_repo, "tracker-agent")
    assert result.success
    assert any("new_file.py" in f for f in result.changed_files)


async def test_no_changes_returns_empty_list(git_repo: Path):
    result = await GitManager.commit(git_repo, "no-change-agent")
    assert result.success
    assert result.changed_files == []
```

- [ ] **Step 2: Run all tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_git_manager.py -v`
Expected: All tests PASS (approximately 22 tests total)

- [ ] **Step 3: Commit**

```bash
git add packages/core/tests/test_git_manager.py
git commit -m "test(core): add concurrency, retry, and error handling tests for GitManager"
```

---

### Task 5: Update AgentExecutor call sites

**Files:**
- Modify: `packages/core/src/shannon_core/agents/executor.py`

- [ ] **Step 1: Update the 4 GitManager call sites to async**

In `packages/core/src/shannon_core/agents/executor.py`, there are exactly 4 lines that call GitManager methods synchronously. Change each to use `await`:

**Line 55** — `create_checkpoint`: add `await` and pass `attempt` parameter. Since `execute()` does not currently receive `attempt`, keep the default value `1` for now (the caller can add attempt tracking later):

```python
# Before:
        GitManager.create_checkpoint(deliverables, agent_name)

# After:
        await GitManager.create_checkpoint(deliverables, agent_name)
```

**Line 69** — `rollback` on spending cap:

```python
# Before:
            GitManager.rollback(deliverables, "spending cap detected")

# After:
            await GitManager.rollback(deliverables, "spending cap detected")
```

**Line 78** — `rollback` on execution failure:

```python
# Before:
            GitManager.rollback(deliverables, "execution failure")

# After:
            await GitManager.rollback(deliverables, "execution failure")
```

**Line 93** — `commit`:

```python
# Before:
        GitManager.commit(deliverables, agent_name)

# After:
        await GitManager.commit(deliverables, agent_name)
```

- [ ] **Step 2: Verify no other sync GitManager calls remain**

Run: `grep -rn "GitManager\.\(create_checkpoint\|commit\|rollback\|get_commit_hash\)" packages/ --include="*.py" | grep -v test_ | grep -v "await"`
Expected: No output (all calls use `await`)

- [ ] **Step 3: Verify executor module imports cleanly**

Run: `cd /root/shannon-py && python -c "from shannon_core.agents.executor import AgentExecutor; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/agents/executor.py
git commit -m "refactor(core): update AgentExecutor to use async GitManager"
```

---

### Task 6: Full test suite verification

**Files:** None (verification only)

- [ ] **Step 1: Run the full core test suite**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/ -v`
Expected: All tests PASS. If any unrelated tests fail, note them but do not block — only test_git_manager.py must be green.

- [ ] **Step 2: Run a quick smoke test to validate end-to-end async flow**

Run: `cd /root/shannon-py && python -c "
import asyncio
from pathlib import Path
import tempfile, subprocess

async def smoke():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / 'repo'
        repo.mkdir()
        subprocess.run(['git', 'init'], cwd=repo, capture_output=True, check=True)
        subprocess.run(['git', 'config', 'user.email', 'smoke@test.com'], cwd=repo, capture_output=True, check=True)
        subprocess.run(['git', 'config', 'user.name', 'Smoke'], cwd=repo, capture_output=True, check=True)
        (repo / 'a.txt').write_text('a')
        subprocess.run(['git', 'add', '.'], cwd=repo, capture_output=True, check=True)
        subprocess.run(['git', 'commit', '-m', 'init'], cwd=repo, capture_output=True, check=True)

        from shannon_core.git_manager import GitManager
        assert await GitManager.is_git_repository(repo)
        r = await GitManager.create_checkpoint(repo, 'smoke-agent')
        assert r.success
        (repo / 'deliverable.txt').write_text('result')
        r = await GitManager.commit(repo, 'smoke-agent')
        assert r.success and len(r.changed_files) > 0
        h = await GitManager.get_commit_hash(repo)
        assert h and len(h) == 40
        (repo / 'bad.txt').write_text('bad')
        r = await GitManager.rollback(repo, 'smoke test')
        assert r.success
        assert not (repo / 'bad.txt').exists()
        print('ALL SMOKE TESTS PASSED')

asyncio.run(smoke())
"`
Expected: `ALL SMOKE TESTS PASSED`

- [ ] **Step 3: Commit (only if any fixups were needed)**

If any adjustments were made during verification:

```bash
git add -u
git commit -m "fix(core): address test findings from full verification"
```

---

## Self-Review Checklist

### Spec Coverage

| Spec Section | Task |
|---|---|
| 1. `GitResult` dataclass | Task 1 |
| 2. Public API (6 methods) | Task 2 |
| 3. Concurrency control (`asyncio.Lock`) | Task 2 |
| 4. Retry mechanism + lock patterns | Task 2 |
| 5. Two-phase checkpoint strategy | Task 2 (attempt > 1 branch) |
| 6. Change tracking + logging | Task 2 (`_get_changed_files`, `_log_change_summary`) |
| 7. Error handling (`PentestError`, `retryable`) | Task 2 |
| 8. `is_git_repository` guard | Task 2 |
| Calling-site refactoring | Task 5 |
| Test scenarios 1–7 | Tasks 3, 4 |

### Placeholder Scan

No TBD, TODO, or vague directives. Every step has complete code.

### Type Consistency

- `GitResult` defined in Task 1, used in Task 2 return types, checked in Tasks 3–4
- `PentestError` signature: `message, category, retryable=False, error_code=None, context=None` — consistent across all raise sites
- `AgentName` import and `agent_name.value` pattern preserved from original
- All async methods use `async def` / `await` consistently
