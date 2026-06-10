"""GitNexus CLI integration engine.

Wraps GitNexus CLI commands (analyze, context) as subprocess calls.
This is the CLI channel of the dual-channel GitNexus integration.
The MCP channel is in gitnexus_mcp.py.
"""

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class IndexResult:
    """Result of an ensure_indexed() call."""

    success: bool
    file_count: int = 0
    symbol_count: int = 0
    is_stale: bool = False
    error_message: str | None = None


class GitNexusError(Exception):
    """Error raised when GitNexus operations fail."""
    pass


class GitNexusEngine:
    """GitNexus CLI integration engine.

    Usage:
        engine = GitNexusEngine(repo_root)
        result = engine.ensure_indexed()           # gitnexus analyze
        stale = engine.check_stale()               # check if index is stale
        ctx = engine.get_context("func")           # gitnexus context --name func
    """

    def __init__(self, repo_root: Path, timeout: int = 300):
        self.repo_root = repo_root
        self.gitnexus_dir = repo_root / ".gitnexus"
        self.timeout = timeout

    def is_available(self) -> bool:
        """Check if gitnexus CLI is installed."""
        return shutil.which("gitnexus") is not None

    def ensure_indexed(self, force: bool = False) -> IndexResult:
        """Run gitnexus analyze if not already indexed.

        Creates .gitnexus/ directory with the knowledge graph.
        Skips if .gitnexus/ already exists (unless force=True).

        Args:
            force: If True, run analyze even when index already exists.

        Returns:
            IndexResult with success status and metadata.
        """
        if not force and self.gitnexus_dir.exists():
            logger.debug("GitNexus index already exists at %s", self.gitnexus_dir)
            return IndexResult(success=True, is_stale=False)

        args = ["analyze", str(self.repo_root)]
        if force:
            args.append("--force")

        try:
            logger.info("Running gitnexus analyze on %s", self.repo_root)
            self._run_cli(*args)
            logger.info("GitNexus indexing complete")
        except GitNexusError as exc:
            return IndexResult(success=False, error_message=str(exc))

        return IndexResult(success=True)

    def check_stale(self) -> bool:
        """Check if the index is stale (older than latest commit).

        Compares .gitnexus/ directory mtime with the timestamp of the
        latest git commit.

        Returns:
            True if index is stale or missing, False if fresh or
            unable to determine (no git repo).
        """
        if not (self.repo_root / ".git").exists():
            return False

        if not self.gitnexus_dir.exists():
            return True

        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%ct"],
                capture_output=True, text=True, timeout=10,
                cwd=str(self.repo_root),
            )
            if result.returncode != 0:
                return False
            commit_ts = int(result.stdout.strip())
        except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
            return False

        index_ts = self.gitnexus_dir.stat().st_mtime
        return index_ts < commit_ts

    def get_context(self, symbol_name: str) -> dict:
        """Get 360-degree context for a symbol.

        Equivalent to SCR-AI's GitNexusChainBuilder._query_context().

        Returns:
            {"outgoing": {"calls": [...]}, "incoming": {...}, "processes": [...]}
        """
        result = self._run_cli(
            "context", "--name", symbol_name,
            "--repo", str(self.repo_root),
        )
        return json.loads(result)

    def _run_cli(self, command: str, *args: str) -> str:
        """Execute a gitnexus CLI command and return stdout.

        Raises:
            GitNexusError: If the command fails or times out.
        """
        cmd = ["gitnexus", command, *args]
        logger.debug("Running: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitNexusError(
                f"gitnexus {command} timed out after {self.timeout}s"
            ) from exc
        except FileNotFoundError as exc:
            raise GitNexusError(
                f"gitnexus command not found. Install GitNexus first."
            ) from exc

        if result.returncode != 0:
            raise GitNexusError(
                f"gitnexus {command} failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )

        return result.stdout
