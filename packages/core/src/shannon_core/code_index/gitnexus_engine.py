"""GitNexus CLI integration engine.

Wraps GitNexus CLI commands (analyze, context) as subprocess calls.
This is the CLI channel of the dual-channel GitNexus integration.
The MCP channel is in gitnexus_mcp.py.
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class GitNexusError(Exception):
    """Error raised when GitNexus operations fail."""
    pass


class GitNexusEngine:
    """GitNexus CLI integration engine.

    Usage:
        engine = GitNexusEngine(repo_root)
        engine.ensure_indexed()           # gitnexus analyze
        ctx = engine.get_context("func")  # gitnexus context --name func
    """

    def __init__(self, repo_root: Path, timeout: int = 300):
        self.repo_root = repo_root
        self.gitnexus_dir = repo_root / ".gitnexus"
        self.timeout = timeout

    def is_available(self) -> bool:
        """Check if gitnexus CLI is installed."""
        return shutil.which("gitnexus") is not None

    def ensure_indexed(self) -> None:
        """Run gitnexus analyze if not already indexed.

        Creates .gitnexus/ directory with the knowledge graph.
        Skips if .gitnexus/ already exists.
        """
        if self.gitnexus_dir.exists():
            logger.debug("GitNexus index already exists at %s", self.gitnexus_dir)
            return

        logger.info("Running gitnexus analyze on %s", self.repo_root)
        self._run_cli("analyze", str(self.repo_root))
        logger.info("GitNexus indexing complete")

    def get_context(self, symbol_name: str) -> dict:
        """Get 360° context for a symbol.

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
