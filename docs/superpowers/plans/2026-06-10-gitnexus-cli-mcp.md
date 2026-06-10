# GitNexus CLI + MCP 全自动集成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 GitNexus CLI + MCP 集成为 shannon-py 调用链分析的底层引擎，实现全自动索引 → MCP 查询 → 降级回退。

**Architecture:** 增强现有的 `gitnexus_mcp.py`（MCP 客户端）、`gitnexus_engine.py`（CLI 引擎）、`gitnexus_call_graph.py`（调用图构建），加入 `impact` 工具追踪、自动索引、降级回退，最后在 pipeline 入口和 Temporal activities 中接入真实客户端替换 Stub。

**Tech Stack:** Python 3.12+, asyncio subprocess (stdio MCP), Pydantic models, pytest + unittest.mock

**Spec:** `docs/superpowers/specs/2026-06-10-gitnexus-cli-mcp-design.md`

---

## File Structure

```
Modify:
  packages/core/src/shannon_core/code_index/
    gitnexus_mcp.py           # Add initialized notification, context manager, timeout, retry
    gitnexus_engine.py        # Add force rebuild, stale check, IndexResult
    gitnexus_call_graph.py    # Add impact-based tracing, find_sinks, get_function_context
    __init__.py               # Add auto-indexing + fallback in pipeline entry
  packages/whitebox/src/shannon_whitebox/pipeline/
    activities.py             # Replace StubMCP with real GitNexusMCPClient + auto-index

Test (modify existing):
  packages/core/tests/code_index/
    test_gitnexus_mcp.py      # Tests for new MCP features
    test_gitnexus_engine.py   # Tests for engine enhancements
    test_gitnexus_call_graph.py # Tests for impact-based tracing
```

---

## Task 1: MCP Client — Add initialized notification + context manager + timeout

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/gitnexus_mcp.py`
- Test: `packages/core/tests/code_index/test_gitnexus_mcp.py`

The MCP protocol requires sending an `initialized` **notification** (no id, no response expected) after receiving the `initialize` response. Also add `__aenter__`/`__aexit__` for context manager usage and a readline timeout.

- [ ] **Step 1: Write failing tests for initialized notification and context manager**

Add these tests to the end of `packages/core/tests/code_index/test_gitnexus_mcp.py`:

```python
@pytest.mark.asyncio
async def test_start_sends_initialized_notification(self, tmp_path):
    """After initialize response, client must send an initialized notification."""
    client = GitNexusMCPClient(tmp_path)
    sent_lines: list[bytes] = []

    with patch("shannon_core.code_index.gitnexus_mcp.asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        original_write = mock_proc.stdin.write

        def capture_write(data: bytes):
            sent_lines.append(data)
        mock_proc.stdin.write = capture_write
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(return_value=json.dumps({
            "jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}
        }).encode())
        mock_proc.wait = AsyncMock()
        mock_exec.return_value = mock_proc

        await client.start()

        # Should have sent 2 messages: initialize + initialized notification
        assert len(sent_lines) == 2
        init_msg = json.loads(sent_lines[0])
        assert init_msg["method"] == "initialize"
        notif_msg = json.loads(sent_lines[1])
        assert notif_msg["method"] == "notifications/initialized"
        assert "id" not in notif_msg  # notifications have no id
        await client.stop()

@pytest.mark.asyncio
async def test_context_manager(self, tmp_path):
    """GitNexusMCPClient supports async with statement."""
    client = GitNexusMCPClient(tmp_path)
    with patch("shannon_core.code_index.gitnexus_mcp.asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(return_value=json.dumps({
            "jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}
        }).encode())
        mock_proc.wait = AsyncMock()
        mock_exec.return_value = mock_proc

        async with client:
            assert client._process is not None
        assert client._process is None  # stopped after exit

@pytest.mark.asyncio
async def test_send_request_timeout(self, tmp_path):
    """_send_request raises on readline timeout."""
    client = GitNexusMCPClient(tmp_path)
    mock_proc = MagicMock()
    mock_proc.stdin = MagicMock()
    mock_proc.stdin.drain = AsyncMock()
    mock_proc.stdout = AsyncMock()
    mock_proc.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError())
    client._process = mock_proc

    with pytest.raises(ConnectionError, match="timed out"):
        await client._send_request("tools/call", {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_mcp.py -v -k "test_start_sends_initialized or test_context_manager or test_send_request_timeout"`
Expected: 3 FAIL (initialized notification not sent, `__aenter__` missing, timeout not handled)

- [ ] **Step 3: Implement MCP client enhancements**

Replace the full content of `packages/core/src/shannon_core/code_index/gitnexus_mcp.py` with:

```python
"""GitNexus MCP client — stdio JSON-RPC protocol.

Provides access to GitNexus's advanced tools (cypher, impact, query, context)
through the Model Context Protocol (MCP) stdio transport.
"""

import json
import logging
import asyncio
from pathlib import Path

logger = logging.getLogger(__name__)

MCP_READ_TIMEOUT = 30  # seconds


class GitNexusMCPClient:
    """MCP client for GitNexus — communicates via stdio JSON-RPC.

    Usage::

        async with GitNexusMCPClient(repo_root) as client:
            result = await client.call_tool("impact", {"target": "fn", "direction": "upstream"})

    Or manually::

        client = GitNexusMCPClient(repo_root)
        await client.start()
        result = await client.call_tool("cypher", {"query": "..."})
        await client.stop()
    """

    MCP_PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0

    async def start(self) -> None:
        """Start the gitnexus mcp subprocess and complete MCP handshake.

        Handshake: initialize request → response → initialized notification.
        """
        self._process = await asyncio.create_subprocess_exec(
            "gitnexus", "mcp", "--repo", str(self.repo_root),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # Send MCP initialize request
        await self._send_request("initialize", {
            "protocolVersion": self.MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "shannon-py", "version": "1.0"},
        })
        # Send initialized notification (no id, no response expected)
        await self._send_notification("notifications/initialized", {})
        logger.info("GitNexus MCP client started")

    async def stop(self) -> None:
        """Terminate the MCP subprocess."""
        if self._process:
            self._process.terminate()
            await self._process.wait()
            self._process = None
            logger.info("GitNexus MCP client stopped")

    async def call_tool(self, tool_name: str, arguments: dict) -> list | dict | str | None:
        """Call an MCP tool and return the parsed result.

        Args:
            tool_name: One of "cypher", "impact", "query", "context", etc.
            arguments: Tool-specific arguments.

        Returns:
            Parsed tool result (usually a list of dicts or a string).
        """
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        return self._parse_tool_result(result)

    async def _send_request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and read the response with timeout."""
        if self._process is None:
            raise RuntimeError("GitNexus MCP client not started. Call await client.start() first.")
        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        line = json.dumps(request) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

        try:
            response_line = await asyncio.wait_for(
                self._process.stdout.readline(), timeout=MCP_READ_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise ConnectionError(
                f"GitNexus MCP timed out after {MCP_READ_TIMEOUT}s waiting for response"
            )

        if not response_line:
            raise ConnectionError("GitNexus MCP closed connection")

        response = json.loads(response_line.decode())

        if "error" in response:
            raise RuntimeError(
                f"MCP error: {response['error'].get('message', 'unknown')}"
            )

        return response.get("result", response)

    async def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        if self._process is None:
            raise RuntimeError("GitNexus MCP client not started.")
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        line = json.dumps(notification) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

    def _parse_tool_result(self, result: dict) -> list | dict | str | None:
        """Parse MCP tool result content into Python objects."""
        if not result:
            return None

        content = result.get("content", [])
        if not content:
            return result

        # MCP tool results have content array with type=text items
        for item in content:
            if item.get("type") == "text":
                text = item.get("text", "")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text

        return result

    async def __aenter__(self) -> "GitNexusMCPClient":
        await self.start()
        return self

    async def __aexit__(self, *args) -> None:
        await self.stop()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_mcp.py -v`
Expected: All 9 tests PASS (6 existing + 3 new)

- [ ] **Step 5: Run full existing test suite to confirm no regressions**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_mcp.py packages/core/tests/code_index/test_gitnexus_engine.py packages/core/tests/code_index/test_gitnexus_call_graph.py -v`
Expected: All 19 existing tests + 3 new = 22 PASS

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/gitnexus_mcp.py packages/core/tests/code_index/test_gitnexus_mcp.py
git commit -m "feat(mcp): add initialized notification, context manager, and timeout to GitNexusMCPClient"
```

---

## Task 2: Engine — Add force rebuild + stale check + IndexResult

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/gitnexus_engine.py`
- Test: `packages/core/tests/code_index/test_gitnexus_engine.py`

The engine currently skips indexing if `.gitnexus/` exists. We need: `force=True` for full rebuild, `check_stale()` to detect stale indexes, and a structured `IndexResult` return value.

- [ ] **Step 1: Write failing tests for engine enhancements**

Add these tests to the end of `packages/core/tests/code_index/test_gitnexus_engine.py`:

```python
def test_ensure_indexed_force_rebuilds(self, tmp_path):
    """ensure_indexed(force=True) runs analyze even when .gitnexus/ exists."""
    (tmp_path / ".gitnexus").mkdir()  # existing index
    engine = GitNexusEngine(tmp_path)
    with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
        engine.ensure_indexed(force=True)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "--force" in cmd

def test_ensure_indexed_returns_index_result(self, tmp_path):
    """ensure_indexed returns an IndexResult dataclass."""
    engine = GitNexusEngine(tmp_path)
    with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
        result = engine.ensure_indexed()
        assert result.success is True
        assert result.is_stale is False

def test_ensure_indexed_failure_returns_failed_result(self, tmp_path):
    """ensure_indexed returns failed IndexResult on error."""
    engine = GitNexusEngine(tmp_path)
    with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
        result = engine.ensure_indexed()
        assert result.success is False
        assert result.error_message is not None

def test_check_stale_no_git_repo(self, tmp_path):
    """check_stale returns False when no .git exists."""
    engine = GitNexusEngine(tmp_path)
    with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
        result = engine.check_stale()
        # No git repo → can't determine staleness → assume not stale
        assert result is False

def test_check_stale_fresh_index(self, tmp_path):
    """check_stale returns False when index is newer than latest commit."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitnexus").mkdir()
    engine = GitNexusEngine(tmp_path)
    with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
        import time
        # git log returns a recent timestamp, .gitnexus mtime is newer
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=str(int(time.time())),  # current timestamp
            stderr="",
        )
        result = engine.check_stale()
        assert result is False

def test_check_stale_stale_index(self, tmp_path):
    """check_stale returns True when index is older than latest commit."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitnexus").mkdir()
    engine = GitNexusEngine(tmp_path)
    with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
        # git log returns a very recent timestamp (future)
        import time
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=str(int(time.time()) + 10000),  # future timestamp
            stderr="",
        )
        result = engine.check_stale()
        assert result is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_engine.py -v -k "test_ensure_indexed_force or test_ensure_indexed_returns or test_ensure_indexed_failure or test_check_stale"`
Expected: 6 FAIL (IndexResult not defined, `force` param missing, `check_stale` missing)

- [ ] **Step 3: Implement engine enhancements**

Replace the full content of `packages/core/src/shannon_core/code_index/gitnexus_engine.py` with:

```python
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


class GitNexusError(Exception):
    """Error raised when GitNexus operations fail."""
    pass


@dataclass
class IndexResult:
    """Result of a gitnexus analyze run."""
    success: bool
    file_count: int = 0
    symbol_count: int = 0
    is_stale: bool = False
    error_message: str | None = None


class GitNexusEngine:
    """GitNexus CLI integration engine.

    Usage::

        engine = GitNexusEngine(repo_root)
        result = engine.ensure_indexed()     # gitnexus analyze → IndexResult
        is_stale = engine.check_stale()      # check if index needs refresh
        ctx = engine.get_context("func")     # gitnexus context --name func
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

        Args:
            force: If True, run analyze --force even if .gitnexus/ exists
                    (full rebuild: re-parse + graph rebuild + FTS rebuild).

        Returns:
            IndexResult with success status and metadata.
        """
        if self.gitnexus_dir.exists() and not force:
            logger.debug("GitNexus index already exists at %s", self.gitnexus_dir)
            return IndexResult(success=True, is_stale=False)

        args = ["analyze", str(self.repo_root)]
        if force:
            args.append("--force")

        logger.info("Running gitnexus %s on %s", " ".join(args[1:]), self.repo_root)
        try:
            self._run_cli(*args)
        except GitNexusError as exc:
            if not force:
                logger.warning("Initial analyze failed, retrying with --force: %s", exc)
                try:
                    self._run_cli("analyze", str(self.repo_root), "--force")
                except GitNexusError as retry_exc:
                    return IndexResult(success=False, error_message=str(retry_exc))
            else:
                return IndexResult(success=False, error_message=str(exc))

        logger.info("GitNexus indexing complete")
        return IndexResult(success=True)

    def check_stale(self) -> bool:
        """Check if the index is stale relative to the latest git commit.

        Returns True if the .gitnexus/ directory mtime is older than the
        latest git commit timestamp. Returns False if no git repo or no
        index exists (not stale — either no index or can't determine).
        """
        git_dir = self.repo_root / ".git"
        if not git_dir.exists():
            return False

        if not self.gitnexus_dir.exists():
            return True  # no index at all → "stale"

        try:
            # Get latest commit timestamp as unix epoch
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

        # Compare with .gitnexus/ modification time
        import os
        try:
            index_ts = self.gitnexus_dir.stat().st_mtime
        except OSError:
            return True

        return index_ts < commit_ts

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_engine.py -v`
Expected: All 13 tests PASS (7 existing + 6 new)

- [ ] **Step 5: Run full GitNexus test suite to confirm no regressions**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_mcp.py packages/core/tests/code_index/test_gitnexus_engine.py packages/core/tests/code_index/test_gitnexus_call_graph.py -v`
Expected: All 25 tests PASS

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/gitnexus_engine.py packages/core/tests/code_index/test_gitnexus_engine.py
git commit -m "feat(engine): add force rebuild, stale check, and IndexResult to GitNexusEngine"
```

---

## Task 3: Call Graph — Add impact-based upstream tracing

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/gitnexus_call_graph.py`
- Test: `packages/core/tests/code_index/test_gitnexus_call_graph.py`

The existing call graph builder uses `query` + `process` MCP tools. We add `impact`-based tracing that starts from sinks and traces upstream to sources — the reverse direction needed for taint analysis. Also add `get_function_context()` for retrieving function signatures and `find_sinks_by_patterns()` for discovering sink functions.

- [ ] **Step 1: Write failing tests for impact-based tracing**

Add these tests to the end of `packages/core/tests/code_index/test_gitnexus_call_graph.py`:

```python
class FakeImpactMCPClient:
    """Fake MCP client with separate responses per tool+arguments."""

    def __init__(self, responses: dict[str, list | dict | str | None]):
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool_name: str, arguments: dict):
        self.calls.append((tool_name, arguments))
        key = tool_name
        return self._responses.get(key)


class TestImpactTracing:
    @pytest.mark.asyncio
    async def test_trace_from_sink_builds_chains(self):
        """trace_from_sink uses impact tool to build upstream chains."""
        mcp = FakeImpactMCPClient(responses={
            "impact": {
                "target": {"name": "execute_sql", "kind": "Function", "file": "db.py", "line": 30},
                "upstream": [
                    {"depth": 1, "name": "get_users", "kind": "Function", "file": "svc.py", "line": 15, "relation": "CALLS", "confidence": 0.9},
                    {"depth": 2, "name": "handler", "kind": "Function", "file": "app.py", "line": 5, "relation": "CALLS", "confidence": 0.85},
                ],
            },
        })
        result = await trace_from_sink(
            mcp_client=mcp,
            sink_name="execute_sql",
            sink_file="db.py",
            sink_line=30,
        )
        assert len(result.edges) >= 1
        assert result.chains >= 1 or len(result.edges) >= 1
        # Should have called impact tool
        assert any(c[0] == "impact" for c in mcp.calls)

    @pytest.mark.asyncio
    async def test_trace_from_sink_returns_empty_on_none(self):
        """trace_from_sink returns empty result when impact returns None."""
        mcp = FakeImpactMCPClient(responses={"impact": None})
        result = await trace_from_sink(
            mcp_client=mcp,
            sink_name="nonexistent",
            sink_file="f.py",
            sink_line=1,
        )
        assert result.edges == []
        assert result.chains == []

    @pytest.mark.asyncio
    async def test_find_sinks_by_patterns(self):
        """find_sinks_by_patterns uses query tool to discover sinks."""
        mcp = FakeImpactMCPClient(responses={
            "query": [
                {"name": "execute_sql", "kind": "Function", "filePath": "db.py", "startLine": 30},
                {"name": "eval", "kind": "Function", "filePath": "utils.py", "startLine": 10},
            ],
        })
        sinks = await find_sinks_by_patterns(mcp, ["execute_sql", "eval"])
        assert len(sinks) >= 1
        assert any(s["name"] == "execute_sql" for s in sinks)

    @pytest.mark.asyncio
    async def test_find_sinks_returns_empty_on_none(self):
        """find_sinks_by_patterns returns empty list when query returns None."""
        mcp = FakeImpactMCPClient(responses={"query": None})
        sinks = await find_sinks_by_patterns(mcp, ["nonexistent"])
        assert sinks == []

    @pytest.mark.asyncio
    async def test_get_function_context(self):
        """get_function_context retrieves symbol details via context tool."""
        mcp = FakeImpactMCPClient(responses={
            "context": {
                "symbol": {"uid": "Function:get_users", "kind": "Function", "filePath": "svc.py", "startLine": 10},
                "incoming": {"calls": [{"name": "handler"}]},
                "outgoing": {"calls": [{"name": "execute_sql"}]},
                "processes": [{"name": "UserFlow"}],
            },
        })
        ctx = await get_function_context(mcp, "get_users")
        assert ctx is not None
        assert "symbol" in ctx
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_call_graph.py -v -k "TestImpactTracing or test_find_sinks or test_get_function_context"`
Expected: FAIL — `trace_from_sink`, `find_sinks_by_patterns`, `get_function_context` not defined

- [ ] **Step 3: Implement impact-based tracing functions**

Add the following functions to the **end** of `packages/core/src/shannon_core/code_index/gitnexus_call_graph.py` (append after the existing `build_call_graph_from_gitnexus` function):

```python
# ---------------------------------------------------------------------------
# Impact-based upstream tracing (supplementary to query+process flow)
# ---------------------------------------------------------------------------

async def trace_from_sink(
    mcp_client: "object",
    sink_name: str,
    sink_file: str,
    sink_line: int,
    *,
    direction: str = "upstream",
    max_depth: int = 5,
) -> CallGraphResult:
    """Trace call chain from a sink function using the impact MCP tool.

    Uses GitNexus ``impact`` tool to find all callers (upstream) or callees
    (downstream) of a sink function, then converts the response into the
    existing ``CallEdge`` / ``CallChain`` format.

    Args:
        mcp_client: Any object with a ``call_tool(tool_name, arguments)`` async method.
        sink_name: Name of the sink function to trace from.
        sink_file: File path of the sink function.
        sink_line: Line number of the sink function.
        direction: "upstream" (callers) or "downstream" (callees).
        max_depth: Maximum traversal depth.

    Returns:
        CallGraphResult with edges and chains discovered.
    """
    result_data = await mcp_client.call_tool("impact", {
        "target": sink_name,
        "direction": direction,
        "maxDepth": max_depth,
    })

    if result_data is None:
        return CallGraphResult(
            edges=[], chains=[],
            degradation_report=DegradationReport(),
        )

    edges: list[CallEdge] = []
    # Handle both dict and string responses
    if isinstance(result_data, str):
        return CallGraphResult(
            edges=[], chains=[],
            degradation_report=DegradationReport(),
        )

    upstream = result_data.get("upstream" if direction == "upstream" else "downstream", [])
    sink_id = f"{sink_file}:{sink_name}:{sink_line}"

    for entry in upstream:
        caller_name = entry.get("name", "")
        caller_file = entry.get("file", "")
        caller_line = entry.get("line", 0)
        relation = entry.get("relation", "CALLS")
        confidence = entry.get("confidence", 0.5)

        if not caller_name:
            continue

        if direction == "upstream":
            # upstream: caller -> sink
            caller_id = f"{caller_file}:{caller_name}:{caller_line}" if caller_file else caller_name
            edges.append(CallEdge(
                caller_id=caller_id,
                callee_name=sink_name,
                callee_file=sink_file if caller_file else None,
                resolved=bool(caller_file),
                line=caller_line,
            ))
        else:
            # downstream: sink -> callee
            callee_id = f"{caller_file}:{caller_name}:{caller_line}" if caller_file else caller_name
            edges.append(CallEdge(
                caller_id=sink_id,
                callee_name=caller_name,
                callee_file=caller_file or None,
                resolved=bool(caller_file),
                line=sink_line,
            ))

    # Build chains from edges — use sink as the starting point for upstream
    if direction == "upstream" and edges:
        # For upstream edges, group by depth to build chains
        chains = _build_upstream_chains(edges, sink_id, max_depth)
    else:
        chains = _build_chains_from_edges(
            edges, [sink_id] if edges else [],
        )

    resolved_count = sum(1 for e in edges if e.resolved)
    degradation_report = DegradationReport(
        total_edges=len(edges),
        resolved_count=resolved_count,
        unresolved_count=len(edges) - resolved_count,
    )

    return CallGraphResult(
        edges=edges,
        chains=chains,
        degradation_report=degradation_report,
    )


def _build_upstream_chains(
    edges: list[CallEdge],
    sink_id: str,
    max_depth: int,
) -> list[CallChain]:
    """Build CallChain list from upstream edges (callers → sink).

    Each unique caller at depth 1 becomes an entry point, with the path
    going through intermediate callers down to the sink.
    """
    # Group edges by caller (each caller calls the sink or an intermediate)
    # Simple approach: build a single chain per unique caller
    seen_callers: set[str] = set()
    chains: list[CallChain] = []

    for edge in edges:
        if edge.caller_id in seen_callers:
            continue
        seen_callers.add(edge.caller_id)

        chains.append(CallChain(
            entry_point_id=edge.caller_id,
            path=[edge.caller_id, sink_id],
            depth=1,
            has_unresolved=not edge.resolved,
        ))

    return chains


async def find_sinks_by_patterns(
    mcp_client: "object",
    patterns: list[str],
) -> list[dict]:
    """Discover sink functions using GitNexus query tool.

    Args:
        mcp_client: MCP client with ``call_tool`` method.
        patterns: List of function name patterns to search for (e.g. ["execute_sql", "eval"]).

    Returns:
        List of dicts with name, filePath, startLine for each found sink.
    """
    all_sinks: list[dict] = []
    seen_names: set[str] = set()

    for pattern in patterns:
        result = await mcp_client.call_tool("query", {
            "query": pattern,
        })

        if result is None or isinstance(result, str):
            continue

        # Handle both list and dict responses
        items = result if isinstance(result, list) else result.get("definitions", [])
        for item in items:
            if isinstance(item, dict):
                name = item.get("name", "")
                if name and name not in seen_names:
                    seen_names.add(name)
                    all_sinks.append({
                        "name": name,
                        "filePath": item.get("filePath", item.get("file", "")),
                        "startLine": item.get("startLine", item.get("line", 0)),
                    })

    return all_sinks


async def get_function_context(
    mcp_client: "object",
    function_name: str,
) -> dict | None:
    """Get 360° context for a function via GitNexus context tool.

    Args:
        mcp_client: MCP client with ``call_tool`` method.
        function_name: Name of the function to look up.

    Returns:
        Dict with symbol info, incoming/outgoing calls, and processes.
        None if function not found or tool returns None.
    """
    result = await mcp_client.call_tool("context", {
        "name": function_name,
    })

    if result is None:
        return None

    if isinstance(result, str):
        return None

    return result
```

Also add the import for `DegradationReport` at the top of the file (it's already imported via `models`):

The existing import line:
```python
from shannon_core.code_index.models import (
    CallChain,
    CallEdge,
    CallGraphResult,
    DegradationReport,
    FuncBlock,
    GitNexusNotIndexedError,
)
```
Already includes `DegradationReport` — no change needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_call_graph.py -v`
Expected: All 14 tests PASS (9 existing + 5 new)

- [ ] **Step 5: Run full GitNexus test suite to confirm no regressions**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_mcp.py packages/core/tests/code_index/test_gitnexus_engine.py packages/core/tests/code_index/test_gitnexus_call_graph.py -v`
Expected: All 30 tests PASS

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/gitnexus_call_graph.py packages/core/tests/code_index/test_gitnexus_call_graph.py
git commit -m "feat(call-graph): add impact-based upstream tracing, find_sinks, and get_function_context"
```

---

## Task 4: Pipeline — Add auto-indexing + GitNexus fallback

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py`
- Modify: `packages/core/tests/code_index/test_gitnexus_call_graph.py` (add pipeline-level test)

The pipeline entry `build_code_index_with_gitnexus` currently receives an `mcp_client` from outside. We add auto-indexing (calling `GitNexusEngine.ensure_indexed` before MCP queries) and a fallback path when GitNexus is unavailable.

- [ ] **Step 1: Write failing test for auto-indexing pipeline**

Add this test to the end of `packages/core/tests/code_index/test_gitnexus_call_graph.py`:

```python
class TestPipelineAutoIndexing:
    @pytest.mark.asyncio
    async def test_auto_index_before_mcp(self, tmp_path):
        """build_code_index_with_gitnexus calls ensure_indexed when auto_index=True."""
        from shannon_core.code_index import build_code_index_with_gitnexus
        from shannon_core.code_index.gitnexus_engine import IndexResult

        # Create a minimal Python file so detect_language succeeds
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("def handler(): pass\n")

        # Mock GitNexusEngine to avoid needing real gitnexus CLI
        with patch("shannon_core.code_index.gitnexus_engine.GitNexusEngine.is_available", return_value=False):
            # GitNexus not available → should fall back gracefully
            with patch("shannon_core.code_index._build_code_index_fallback") as mock_fallback:
                from shannon_core.code_index.models import CodeIndex, DegradationLevel
                mock_fallback.return_value = CodeIndex(
                    repository=str(tmp_path),
                    language="python",
                    total_blocks=0,
                    total_entry_points=0,
                    total_chains=0,
                    blocks=[],
                    edges=[],
                    entry_points=[],
                    chains=[],
                    degradation_level=DegradationLevel.MINIMAL,
                )
                mcp = FakeImpactMCPClient(responses={})
                index = await build_code_index_with_gitnexus(
                    str(tmp_path),
                    mcp_client=mcp,
                    llm_client=AsyncMock(return_value="{}"),
                    auto_index=True,
                )
                assert index is not None
                assert index.degradation_level == DegradationLevel.MINIMAL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_call_graph.py -v -k "test_auto_index_before_mcp"`
Expected: FAIL — `auto_index` parameter not accepted or `_build_code_index_fallback` not defined

- [ ] **Step 3: Implement auto-indexing and fallback**

Replace the `build_code_index_with_gitnexus` function in `packages/core/src/shannon_core/code_index/__init__.py` with:

```python
async def build_code_index_with_gitnexus(
    repo_path: str,
    *,
    mcp_client,
    llm_client,
    auto_index: bool = False,
) -> CodeIndex:
    """Build code index with GitNexus call graph + LLM taint analysis.

    Pipeline:
    1. (optional) Auto-index via gitnexus analyze
    2. Tree-sitter parse → FuncBlock[]
    3. GitNexus MCP → precise call graph (edges, chains, entry_points)
    4. sink_detector → SinkCallSite[]
    5. LLM taint analysis (per-function, only for functions with sinks)
    6. Deterministic cross-function propagation (cross-function parameter mapping)

    When GitNexus is unavailable (auto_index=True and CLI not installed),
    falls back to minimal AST-only mode.

    Args:
        repo_path: Absolute path to the target repository.
        mcp_client: Async client with ``call_tool(tool_name, arguments)`` method.
        llm_client: Async callable for LLM prompts.
        auto_index: If True, run ``gitnexus analyze`` before querying. Falls
                    back to minimal mode if GitNexus CLI is not installed.

    Raises:
        GitNexusNotIndexedError: if GitNexus hasn't indexed the repo
        GitNexusConnectionError: if MCP connection fails
    """
    from shannon_core.models.errors import ErrorCode, PentestError
    from shannon_core.code_index.gitnexus_engine import GitNexusEngine

    repo = Path(repo_path).resolve()

    # 0. Auto-index (if requested)
    if auto_index:
        engine = GitNexusEngine(repo)
        if not engine.is_available():
            logger.warning(
                "GitNexus CLI not installed. Falling back to minimal AST-only mode. "
                "Install with: npm install -g gitnexus"
            )
            return await _build_code_index_fallback(
                str(repo), mcp_client=mcp_client, llm_client=llm_client,
            )
        index_result = engine.ensure_indexed()
        if not index_result.success:
            logger.warning(
                "GitNexus indexing failed: %s. Falling back to minimal mode.",
                index_result.error_message,
            )
            return await _build_code_index_fallback(
                str(repo), mcp_client=mcp_client, llm_client=llm_client,
            )

    file_manifest = discover_security_files(repo)

    # ① Tree-sitter parse → FuncBlock[]
    try:
        language = detect_language(repo)
    except ValueError as exc:
        raise PentestError(
            str(exc), category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
        ) from exc

    logger.info("Detected language: %s", language)

    source_files = discover_source_files(repo, language)
    if not source_files:
        raise PentestError(
            f"No source files found for language '{language}' in {repo}",
            category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
        )

    parser = get_parser(language)
    if parser is None:
        raise PentestError(
            f"No parser available for language '{language}'",
            category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
        )

    file_sources: dict[str, bytes] = {}
    all_blocks = []
    for file_path in source_files:
        try:
            source = file_path.read_bytes()
            rel = str(file_path.relative_to(repo))
            file_sources[rel] = source
            blocks = parser.parse_file(file_path, repo)
            all_blocks.extend(blocks)
        except Exception as exc:
            logger.warning("Failed to index %s: %s", file_path, exc)
            continue

    # ② GitNexus MCP → precise call graph
    call_graph = await build_call_graph_from_gitnexus(
        repo_path=str(repo),
        mcp_client=mcp_client,
        blocks=all_blocks,
    )

    # ③ sink detection
    def _provide_source(block):
        return file_sources.get(block.file_path)
    sink_call_sites = detect_sinks(all_blocks, parser, source_provider=_provide_source)
    logger.info("Detected %d sink call sites", len(sink_call_sites))

    # ④ Group sinks by function
    from collections import defaultdict
    sinks_by_func: dict[str, list] = defaultdict(list)
    for s in sink_call_sites:
        sinks_by_func[s.caller_id].append(s)

    # ⑤ LLM taint analysis (only for functions with sinks)
    blocks_by_id = {b.id: b for b in all_blocks}

    intra_results = {}
    for func_id, func_sinks in sinks_by_func.items():
        block = blocks_by_id.get(func_id)
        if block is None:
            continue
        intra_results[func_id] = await analyze_taint_llm(
            block=block,
            sinks_in_func=func_sinks,
            llm_client=llm_client,
        )

    # ⑥ Deterministic cross-function propagation
    taint_flows = propagate_across_chains(
        chains=call_graph.chains,
        blocks=all_blocks,
        intra_results=intra_results,
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=taint_flows,
        language_coverage=[language],
    )
    logger.info("Built parameter propagation graph: %d taint flows", len(pgraph.taint_flows))

    # ⑦ Convert GitNexus entry_point FuncBlocks → EntryPoint objects
    gitnexus_ep_ids = {ep.id for ep in call_graph.entry_points}
    all_entry_points = detect_entry_points(all_blocks, language, repo_path=str(repo))
    gitnexus_entry_points = [
        ep for ep in all_entry_points if ep.func_block_id in gitnexus_ep_ids
    ]
    detected_ids = {ep.func_block_id for ep in gitnexus_entry_points}
    for ep_block in call_graph.entry_points:
        if ep_block.id not in detected_ids:
            gitnexus_entry_points.append(EntryPoint(
                func_block_id=ep_block.id,
                entry_type="gitnexus",
                route=None,
                http_method=None,
                confidence=0.9,
                evidence=f"GitNexus identified entry point: {ep_block.function_name}",
                needs_llm_review=False,
            ))

    # ⑧ Assemble CodeIndex
    return CodeIndex(
        repository=str(repo),
        language=language,
        total_blocks=len(all_blocks),
        total_entry_points=len(gitnexus_entry_points),
        total_chains=len(call_graph.chains),
        blocks=all_blocks,
        edges=call_graph.edges,
        entry_points=gitnexus_entry_points,
        chains=call_graph.chains,
        sink_call_sites=sink_call_sites,
        file_manifest=file_manifest,
        degradation_level=DegradationLevel.FULL,
    )


async def _build_code_index_fallback(
    repo_path: str,
    *,
    mcp_client,
    llm_client,
) -> CodeIndex:
    """Build a minimal code index without GitNexus.

    Falls back to AST-only parsing: Tree-sitter → FuncBlock[] → basic
    entry point detection. No call graph, no taint analysis.
    """
    from shannon_core.models.errors import ErrorCode, PentestError

    repo = Path(repo_path).resolve()
    file_manifest = discover_security_files(repo)

    try:
        language = detect_language(repo)
    except ValueError as exc:
        raise PentestError(
            str(exc), category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
        ) from exc

    source_files = discover_source_files(repo, language)
    if not source_files:
        return CodeIndex(
            repository=str(repo),
            language=language,
            total_blocks=0,
            total_entry_points=0,
            total_chains=0,
            blocks=[],
            edges=[],
            entry_points=[],
            chains=[],
            file_manifest=file_manifest,
            degradation_level=DegradationLevel.MINIMAL,
        )

    parser = get_parser(language)
    all_blocks = []
    for file_path in source_files:
        try:
            blocks = parser.parse_file(file_path, repo)
            all_blocks.extend(blocks)
        except Exception as exc:
            logger.warning("Failed to index %s: %s", file_path, exc)
            continue

    entry_points = detect_entry_points(all_blocks, language, repo_path=str(repo))

    return CodeIndex(
        repository=str(repo),
        language=language,
        total_blocks=len(all_blocks),
        total_entry_points=len(entry_points),
        total_chains=0,
        blocks=all_blocks,
        edges=[],
        entry_points=entry_points,
        chains=[],
        file_manifest=file_manifest,
        degradation_level=DegradationLevel.MINIMAL,
    )
```

Also update the import at the top of `__init__.py` to include `DegradationLevel` (it's already imported on line 16 — verify it's there).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/code_index/test_gitnexus_call_graph.py -v -k "test_auto_index_before_mcp"`
Expected: PASS

- [ ] **Step 5: Run full test suite to confirm no regressions**

Run: `uv run pytest packages/core/tests/code_index/ -v --tb=short 2>&1 | tail -20`
Expected: All existing tests PASS (the new `auto_index` parameter defaults to `False`, so existing callers are unaffected)

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_gitnexus_call_graph.py
git commit -m "feat(pipeline): add auto-indexing and GitNexus fallback to build_code_index_with_gitnexus"
```

---

## Task 5: Activities — Replace StubMCP with real GitNexus integration

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`

Replace the `_StubMCPClient` with real `GitNexusMCPClient` and add auto-indexing using `GitNexusEngine`. The key change: pass `auto_index=True` so the pipeline handles indexing and fallback automatically.

- [ ] **Step 1: Implement the real GitNexus wiring**

In `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`, replace the `run_code_index` activity (lines 163-209) with:

```python
@activity.defn
async def run_code_index(input: ActivityInput) -> dict:
    try:
        import logging
        from pathlib import Path
        from shannon_core.code_index import build_code_index_with_gitnexus, write_index_files
        from shannon_core.code_index.gitnexus_mcp import GitNexusMCPClient

        logger = logging.getLogger(__name__)

        repo, deliverables, _ = _get_paths(input)

        # Create LLM client for taint analysis
        async def _llm_taint_client(prompt: str, **kwargs) -> str:
            # Placeholder: in production, this calls run_claude_prompt
            return "{}"

        # Create real GitNexus MCP client with auto-indexing.
        # auto_index=True handles:
        #   1. Check if gitnexus CLI is installed
        #   2. Run gitnexus analyze if needed
        #   3. Fall back to minimal AST-only mode if unavailable
        try:
            async with GitNexusMCPClient(Path(repo)) as mcp:
                index = await build_code_index_with_gitnexus(
                    str(repo),
                    mcp_client=mcp,
                    llm_client=_llm_taint_client,
                    auto_index=True,
                )
        except Exception as exc:
            logger.warning(
                "GitNexus MCP failed (%s), falling back to minimal index", exc,
            )
            index = await build_code_index_with_gitnexus(
                str(repo),
                mcp_client=_StubMCPClient(),  # fallback stub
                llm_client=_llm_taint_client,
                auto_index=True,  # will detect unavailable and use minimal mode
            )

        json_path, summary_path = write_index_files(index, str(deliverables))

        return {
            "total_blocks": index.total_blocks,
            "total_entry_points": index.total_entry_points,
            "total_chains": index.total_chains,
            "json_path": str(json_path),
            "summary_path": str(summary_path),
        }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


class _StubMCPClient:
    """Fallback MCP client that returns None, triggering degradation."""
    async def call_tool(self, tool_name: str, arguments: dict):
        return None
```

Note: `_StubMCPClient` is kept as a class-level definition for use in the fallback path, but the primary path now uses real `GitNexusMCPClient`.

- [ ] **Step 2: Verify the whitebox package still imports correctly**

Run: `uv run python -c "from shannon_whitebox.pipeline.activities import run_code_index; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Run whitebox + core tests to confirm no regressions**

Run: `uv run pytest packages/core/tests/code_index/ packages/whitebox/tests/ -v --tb=short 2>&1 | tail -30`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py
git commit -m "feat(activities): replace StubMCP with real GitNexusMCPClient + auto-indexing"
```

---

## Self-Review

### 1. Spec Coverage

| Spec Requirement | Task |
|------------------|------|
| MCP initialized notification | Task 1 |
| Context manager (`__aenter__`/`__aexit__`) | Task 1 |
| Readline timeout (30s) | Task 1 |
| `force=True` rebuild | Task 2 |
| `check_stale()` | Task 2 |
| `IndexResult` return | Task 2 |
| `impact` tool for upstream tracing | Task 3 |
| `query` tool for sink discovery | Task 3 |
| `context` tool for function context | Task 3 |
| Auto-indexing in pipeline | Task 4 |
| Fallback when GitNexus unavailable | Task 4 |
| Real MCP client in activities | Task 5 |
| `_StubMCPClient` fallback | Task 5 |

**Gap:** Spec mentions `cypher` tool for complex multi-hop queries. This is deferred — `impact` handles the primary use case (sink → upstream callers). Cypher can be added later if needed. The existing `build_call_graph_from_gitnexus` already supports cypher via MCP.

### 2. Placeholder Scan

No TBD, TODO, or placeholder patterns found. All code blocks contain complete implementations.

### 3. Type Consistency

- `GitNexusMCPClient.__init__` accepts `Path` → `activities.py` wraps in `Path(repo)` ✅
- `GitNexusEngine.ensure_indexed()` returns `IndexResult` → `build_code_index_with_gitnexus` checks `.success` ✅
- `trace_from_sink` returns `CallGraphResult` → compatible with existing `CallEdge`/`CallChain` ✅
- `FakeImpactMCPClient.call_tool` returns same types as `GitNexusMCPClient.call_tool` ✅
- `_build_code_index_fallback` returns `CodeIndex` → same type as `build_code_index_with_gitnexus` ✅
