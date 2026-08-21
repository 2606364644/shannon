"""GitNexus MCP client — stdio JSON-RPC protocol.

Provides access to GitNexus's advanced tools (cypher, impact, query)
through the Model Context Protocol (MCP) stdio transport.
"""

import json
import logging
import asyncio
from pathlib import Path

logger = logging.getLogger(__name__)

MCP_READ_TIMEOUT = 30
# initialize 握手的读超时（独立于查询级 MCP_READ_TIMEOUT）。`gitnexus mcp` 是 node
# 子进程，并发负载下（真机 NodeGoat-20260821-044404：pre-recon LLM subagent 抢 CPU）
# 冷启动 >30s 才完成内部初始化（stderr 'server starting' 在 initialize 30s 读超时
# 之后 1s 才到；空闲实测 1.5s）。查询级 30s 罩 initialize 会把冷启动余量误杀——
# 本窗口只给握手留余量，握手后的查询仍按 30s 坏连接口径处理。
MCP_INIT_TIMEOUT = 120
# Grace period after SIGTERM before escalating to SIGKILL when stopping the
# MCP subprocess. See GitNexusMCPClient.stop().
MCP_STOP_TIMEOUT = 5
# Timeout for spawning the gitnexus mcp subprocess (fork/exec 阶段)。create_subprocess_exec
# 裸 await 无超时:子进程启动偶发卡死(node 冷启动/pipe 建立 race)时无限期挂起,只能靠外层
# activity start_to_close_timeout 兜底 -- 2026-08-04 delivery 扫描 Attempt 1 卡死 44min 的盲区。
# 加超时后偶发卡死在此窗口内自拔 -> activity 重试,不再空等到 45min。
MCP_START_TIMEOUT = 30
# Timeout for flushing a JSON-RPC request to the subprocess stdin。stdin.drain() 在子进程
# 停止消费 stdin(pipe 缓冲满/僵死)时阻塞,裸 await 同上无超时。与 MCP_READ_TIMEOUT 对称
# 罩住「写」侧,补齐 stdio 读写双向超时。
MCP_WRITE_TIMEOUT = 10


def _parse_md_table(markdown: str) -> list[dict]:
    """Parse a GitNexus cypher markdown table into list[dict].

    GitNexus 1.6.7 returns cypher results as ``{"markdown": "| col | col |\\n| --- |\\n| ... |"}``
    rather than raw records. Extract rows into dicts keyed by header name.
    Skip the header row and the ``| --- |`` separator. Rows with a column
    count mismatch are dropped.
    """
    lines = [ln for ln in markdown.strip().split("\n") if ln.strip().startswith("|")]
    if len(lines) < 3:
        return []
    headers = [h.strip() for h in lines[0].strip("|").split("|")]
    rows: list[dict] = []
    for line in lines[2:]:  # skip header (lines[0]) + separator (lines[1])
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells)))
    return rows


class GitNexusMCPClient:
    """MCP client for GitNexus — communicates via stdio JSON-RPC.

    Usage::

        async with GitNexusMCPClient(repo_root) as client:
            result = await client.call_tool("cypher", {"query": "..."})

    Or with explicit start/stop::

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
        self._stderr_task: asyncio.Task | None = None

    async def _drain_stderr(self) -> None:
        """后台读 GitNexus 子进程 stderr 行 -> logger.warning。

        GitNexus 的 pino 结构化日志 + native 模块报错都走 stderr;之前 stderr=DEVNULL
        把它们全吞了,子进程卡死/报错时 workflow.log 里看不到任何 GitNexus 侧信息
        (2026-08-04 delivery 扫描卡死 44min,GitNexus 侧发生了什么全不可见)。reader
        自身出错不影响主流程(诊断辅助),任何异常都静默退出。
        """
        proc = self._process
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break  # EOF
                text = line.decode(errors="replace").rstrip()
                if text:
                    logger.warning("GitNexus MCP stderr: %s", text)
        except Exception as exc:
            logger.debug("GitNexus MCP stderr drain ended: %s", exc)

    async def start(self) -> None:
        """Start the gitnexus mcp subprocess and send initialize."""
        # NOTE: `gitnexus mcp` does NOT accept --repo. It discovers all
        # indexed repos from the global registry (~/.gitnexus/registry.json).
        # The repo must be indexed via `gitnexus analyze` BEFORE starting MCP.
        try:
            self._process = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "gitnexus", "mcp",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    # 不再 DEVNULL:接出 GitNexus pino 日志 + native 报错,卡死时有诊断。
                    stderr=asyncio.subprocess.PIPE,
                    limit=4 * 1024 * 1024,  # readline 默认 64KB 限制会崩全量 cypher；提到 4MB
                ),
                timeout=MCP_START_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise ConnectionError(
                f"GitNexus MCP subprocess spawn timed out after {MCP_START_TIMEOUT}s"
            ) from exc
        # 后台转发 stderr -> logger(见 _drain_stderr)。
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        # Send MCP initialize request（读超时用 MCP_INIT_TIMEOUT：node 子进程冷启动
        # 在并发负载下 >30s，与查询级 30s 分离，见常量注释）
        await self._send_request("initialize", {
            "protocolVersion": self.MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "supernova", "version": "1.0"},
        }, timeout=MCP_INIT_TIMEOUT)
        # Send initialized notification (MCP handshake requirement)
        await self._send_notification("notifications/initialized", {})
        logger.info("GitNexus MCP client started")

    async def stop(self) -> None:
        """Terminate the MCP subprocess, escalating to SIGKILL if it ignores SIGTERM.

        A bare ``await self._process.wait()`` blocks forever when the subprocess
        doesn't exit on SIGTERM (e.g. wedged mid-query); in production that only
        gets relieved by the activity's start_to_close_timeout, surfacing as a
        CancelledError long after the real failure. Bound the wait and force-kill.
        """
        if self._process:
            # 先停 stderr reader,避免读正在关闭的 pipe。
            if self._stderr_task is not None and not self._stderr_task.done():
                self._stderr_task.cancel()
                try:
                    await self._stderr_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._stderr_task = None
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=MCP_STOP_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(
                    "GitNexus MCP subprocess ignored SIGTERM, escalating to SIGKILL"
                )
                self._process.kill()
                await self._process.wait()
            self._process = None
            logger.info("GitNexus MCP client stopped")

    async def call_tool(self, tool_name: str, arguments: dict) -> list | dict | str | None:
        """Call an MCP tool and return the parsed result.

        Args:
            tool_name: One of "cypher", "impact", "query", etc.
            arguments: Tool-specific arguments.

        Returns:
            Parsed tool result (usually a dict; None on parse failure).
        """
        # Inject repo (path form; GitNexus schema accepts "name or path").
        # Required when multiple repos are indexed in the global registry
        # (~/.gitnexus/registry.json) — otherwise GitNexus returns
        # 'Error: Multiple repositories indexed...'. Harmless when only one.
        arguments.setdefault("repo", str(self.repo_root))
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        return self._parse_tool_result(result)

    async def read_resource(self, uri: str) -> str:
        """Read an MCP resource and return its concatenated text.

        MCP resource content items are ``{uri, mimeType, text}`` — **no ``type``
        field**, unlike tools/call's ``{type: "text", text}``. We take ``text``
        directly from every content item. Returns ``""`` on empty/missing/
        error (process traces are best-effort; one missing trace must not abort
        the whole call graph build).
        """
        try:
            result = await self._send_request("resources/read", {"uri": uri})
        except Exception as exc:
            logger.warning("GitNexus resource read failed (%s): %s", uri, exc)
            return ""
        if not result:
            return ""
        parts: list[str] = []
        for item in result.get("contents", []) or []:
            if isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
        return "\n".join(parts)

    async def _flush_stdin(self) -> None:
        """drain stdin 带超时;写阻塞(子进程不消费 stdin)时快速失败而非无限挂起。

        与 _send_request 的 stdout.readline 超时(MCP_READ_TIMEOUT)对称,补齐 stdio
        「写」侧超时 -- 裸 await stdin.drain() 是 2026-08-04 卡死的盲区之一。
        """
        try:
            await asyncio.wait_for(self._process.stdin.drain(), timeout=MCP_WRITE_TIMEOUT)
        except asyncio.TimeoutError:
            raise ConnectionError(
                f"GitNexus MCP stdin write timed out after {MCP_WRITE_TIMEOUT}s"
            )

    async def _send_request(self, method: str, params: dict, *, timeout: float | None = None) -> dict:
        """Send a JSON-RPC request and read the response."""
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
        await self._flush_stdin()

        try:
            response_line = await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=timeout if timeout is not None else MCP_READ_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise ConnectionError(
                f"GitNexus MCP timed out after {timeout if timeout is not None else MCP_READ_TIMEOUT}s waiting for response"
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
            raise RuntimeError("GitNexus MCP client not started. Call await client.start() first.")
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        line = json.dumps(notification) + "\n"
        self._process.stdin.write(line.encode())
        await self._flush_stdin()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    def _parse_tool_result(self, result: dict) -> list | dict | str | None:
        """Parse MCP tool result content into Python objects.

        GitNexus 1.6.7 returns ``<JSON object> + trailing human hint`` in one
        text blob (strict ``json.loads`` fails with "Extra data"). Delegate to
        ``_parse_text`` which uses ``raw_decode`` to parse the leading JSON and
        tolerate the trailing hint, decode cypher markdown tables, and return
        ``None`` on non-JSON / ambiguous payloads so downstream ``isinstance``
        guards treat them as empty instead of silently iterating a string.
        """
        if not result:
            return None
        content = result.get("content", [])
        if not content:
            return result
        for item in content:
            if item.get("type") == "text":
                return self._parse_text(item.get("text", ""))
        return result

    @staticmethod
    def _parse_text(text: str) -> list | dict | str | None:
        """Parse one GitNexus tool text blob.

        Returns the leading JSON object (dict/list), with cypher markdown
        tables decoded into ``obj["rows"]``. Returns ``None`` on non-JSON text
        (e.g. ``"Error: Multiple repositories indexed..."``) or
        ``status:"ambiguous"`` so consumers see an empty result, not a string.
        """
        stripped = text.lstrip()
        try:
            obj, _end = json.JSONDecoder().raw_decode(stripped)
        except json.JSONDecodeError:
            logger.warning("GitNexus tool returned non-JSON text: %.120s", stripped)
            return None
        if isinstance(obj, dict):
            if obj.get("status") == "ambiguous":
                logger.warning(
                    "GitNexus tool returned ambiguous result: %.120s",
                    str(obj.get("message", "")),
                )
                return None
            if "markdown" in obj and "row_count" in obj:
                obj["rows"] = _parse_md_table(obj["markdown"])
        return obj
