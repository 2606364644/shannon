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
# Grace period after SIGTERM before escalating to SIGKILL when stopping the
# MCP subprocess. See GitNexusMCPClient.stop().
MCP_STOP_TIMEOUT = 5


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

    async def start(self) -> None:
        """Start the gitnexus mcp subprocess and send initialize."""
        # NOTE: `gitnexus mcp` does NOT accept --repo. It discovers all
        # indexed repos from the global registry (~/.gitnexus/registry.json).
        # The repo must be indexed via `gitnexus analyze` BEFORE starting MCP.
        self._process = await asyncio.create_subprocess_exec(
            "gitnexus", "mcp",
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
            Parsed tool result (usually a list of dicts).
        """
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        return self._parse_tool_result(result)

    async def _send_request(self, method: str, params: dict) -> dict:
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
        await self._process.stdin.drain()

        try:
            response_line = await asyncio.wait_for(
                self._process.stdout.readline(), timeout=MCP_READ_TIMEOUT
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
            raise RuntimeError("GitNexus MCP client not started. Call await client.start() first.")
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        line = json.dumps(notification) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

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
