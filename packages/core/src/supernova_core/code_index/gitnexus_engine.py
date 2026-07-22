"""GitNexus CLI integration engine.

Wraps GitNexus CLI commands (analyze, context) as subprocess calls.
This is the CLI channel of the dual-channel GitNexus integration.
The MCP channel is in gitnexus_mcp.py.
"""

import asyncio
import contextlib
import fcntl
import json
import logging
import os
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

    def _registry_json_path(self) -> Path:
        """全局 registry.json 路径（~/.gitnexus/registry.json）。"""
        return Path.home() / ".gitnexus" / "registry.json"

    def _registry_lock_path(self) -> Path:
        """跨进程 flock 锁文件：串行化 gitnexus index，防 writeRegistry ENOENT race。"""
        return Path.home() / ".gitnexus" / "registry.json.lock"

    def _is_repo_registered(self) -> bool:
        """读 registry.json 检查 repo_root 是否已注册（index 失败时的幂等检查）。

        GitNexus registry.json 是 ``[{"path": "...", ...}]`` 数组；race/并发下另一进程
        可能已成功注册本 repo，此时 index 失败可视为成功（repo 实际已在 registry）。
        文件缺失/坏 JSON → False（保守：无法证明已注册）。
        """
        try:
            data = json.loads(self._registry_json_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        repo_str = str(self.repo_root)
        return isinstance(data, list) and any(
            isinstance(entry, dict) and entry.get("path") == repo_str for entry in data
        )

    @contextlib.contextmanager
    def _registry_lock(self):
        """跨进程 flock 串行化 gitnexus index（同步版，阻塞等锁）。

        GitNexus 1.6.8 ``writeRegistry`` 用固定 ``registry.json.tmp`` + 无锁，并发
        ``gitnexus index`` 会 ENOENT rename race。flock 让同 worker 进程内 + 跨进程
        的 index 调用串行（Docker 单 worker 共享 ~/.gitnexus/ 场景）。
        """
        lock_path = self._registry_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    @contextlib.asynccontextmanager
    async def _registry_lock_async(self):
        """async 版 flock：非阻塞 + asyncio 轮询，不卡 event loop（守 _run_cli_async 可取消性）。"""
        lock_path = self._registry_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.2)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

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
            with self._registry_lock():
                self._run_cli("index", str(self.repo_root))
        except GitNexusError as exc:
            # 幂等检查（flock 双保险）：race/并发下另一进程可能已注册本 repo，
            # 此时 index 失败可视为成功（repo 实际已在 registry，MCP 能发现）。
            if self._is_repo_registered():
                logger.warning(
                    "gitnexus index failed but repo already in registry "
                    "(likely registered by a concurrent process): %s", exc)
            else:
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
            async with self._registry_lock_async():
                await self._run_cli_async("index", str(self.repo_root))
        except GitNexusError as exc:
            # 幂等检查（flock 双保险）：race/并发下另一进程可能已注册本 repo，
            # 此时 index 失败可视为成功（repo 实际已在 registry，MCP 能发现）。
            if self._is_repo_registered():
                logger.warning(
                    "gitnexus index failed but repo already in registry "
                    "(likely registered by a concurrent process): %s", exc)
            else:
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
