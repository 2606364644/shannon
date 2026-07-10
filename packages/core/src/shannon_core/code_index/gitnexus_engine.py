"""GitNexus CLI integration engine.

Wraps GitNexus CLI commands (analyze, context) as subprocess calls.
This is the CLI channel of the dual-channel GitNexus integration.
The MCP channel is in gitnexus_mcp.py.
"""

import asyncio
import contextlib
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
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
        """Run gitnexus analyze if needed, then register the repo into the
        global registry (~/.gitnexus/registry.json) via ``gitnexus index``.

        Creates .gitnexus/ directory with the knowledge graph.
        Skips analyze if .gitnexus/ already exists (unless force=True).

        Why register: ``gitnexus mcp`` discovers indexed repos from the global
        registry, NOT from the in-repo .gitnexus/ directory. ``gitnexus analyze``
        builds .gitnexus/ but does not reliably register the repo (1.6.8); a
        repo whose .gitnexus/ is present but absent from the registry leaves
        ``gitnexus mcp`` discovering 0 repos and deadlocking on every query
        (pre-recon step 0 hangs forever, $0 LLM cost). ``gitnexus index`` is
        idempotent and cheap, so we always run it after ensuring .gitnexus/.

        Args:
            force: If True, run analyze even when index already exists.

        Returns:
            IndexResult with success status and metadata.
        """
        if not force and self.gitnexus_dir.exists():
            logger.debug("GitNexus index already exists at %s", self.gitnexus_dir)
        else:
            args = ["analyze", str(self.repo_root)]
            if force:
                args.append("--force")
            try:
                logger.info("Running gitnexus analyze on %s", self.repo_root)
                self._run_cli(*args)
                logger.info("GitNexus indexing complete")
            except GitNexusError as exc:
                return IndexResult(success=False, error_message=str(exc))

        # Idempotently register the repo into the global registry. Without this
        # the MCP channel deadlocks even though .gitnexus/ is present and fresh.
        try:
            self._run_cli("index", str(self.repo_root))
        except GitNexusError as exc:
            return IndexResult(
                success=False,
                error_message=f"failed to register repo in global registry: {exc}",
            )

        return IndexResult(success=True)

    async def ensure_indexed_async(self, force: bool = False) -> IndexResult:
        """async 版 ensure_indexed：与同步版逻辑逐行镜像，唯一差别用 _run_cli_async。

        供 run_code_index 等 async activity 调用，避免阻塞 Temporal worker event loop
        （见 _run_cli_async）——这是 Ctrl+C 取消不可达的根因。
        """
        if not force and self.gitnexus_dir.exists():
            logger.debug("GitNexus index already exists at %s", self.gitnexus_dir)
        else:
            args = ["analyze", str(self.repo_root)]
            if force:
                args.append("--force")
            try:
                logger.info("Running gitnexus analyze on %s", self.repo_root)
                await self._run_cli_async(*args)
                logger.info("GitNexus indexing complete")
            except GitNexusError as exc:
                return IndexResult(success=False, error_message=str(exc))

        # Idempotently register the repo into the global registry. Without this
        # the MCP channel deadlocks even though .gitnexus/ is present and fresh.
        try:
            await self._run_cli_async("index", str(self.repo_root))
        except GitNexusError as exc:
            return IndexResult(
                success=False,
                error_message=f"failed to register repo in global registry: {exc}",
            )

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

    async def _run_cli_async(self, command: str, *args: str) -> str:
        """async 版 _run_cli：用 asyncio.create_subprocess_exec，cancel 时 kill 子进程。

        与 _run_cli 行为等价（超时 / 非零退出 / 找不到命令的错误语义对齐）。async 化是为
        了让 run_code_index activity 不阻塞 Temporal worker event loop——cancel 能在此处的
        await 点注入，进而 proc.kill() 杀掉 gitnexus 子进程，消除 300s 阻塞导致的取消卡死。
        """
        cmd = ["gitnexus", command, *args]
        logger.debug("Running (async): %s", " ".join(cmd))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise GitNexusError(
                "gitnexus command not found. Install GitNexus first."
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            await self._kill_proc(proc)
            raise GitNexusError(
                f"gitnexus {command} timed out after {self.timeout}s"
            )
        except asyncio.CancelledError:
            # 协作式取消：kill 子进程后向上传播，让 activity 在 await 点快速退出
            await self._kill_proc(proc)
            raise

        if proc.returncode != 0:
            err = stderr.decode().strip() if isinstance(stderr, (bytes, bytearray)) else str(stderr).strip()
            raise GitNexusError(
                f"gitnexus {command} failed (exit {proc.returncode}): {err}"
            )
        return stdout.decode() if isinstance(stdout, (bytes, bytearray)) else str(stdout)

    @staticmethod
    async def _kill_proc(proc) -> None:
        """best-effort kill + reap 子进程（cancel / 超时路径）。"""
        with contextlib.suppress(Exception):  # noqa: BLE001
            proc.kill()
        with contextlib.suppress(Exception):  # noqa: BLE001
            await proc.wait()
