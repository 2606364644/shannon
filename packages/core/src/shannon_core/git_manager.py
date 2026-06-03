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
