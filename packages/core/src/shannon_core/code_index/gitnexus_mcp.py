"""GitNexus MCP client — stdio JSON-RPC protocol.

Provides access to GitNexus's advanced tools (cypher, impact, query)
through the Model Context Protocol (MCP) stdio transport.
"""

import json
import logging
import asyncio
from pathlib import Path

logger = logging.getLogger(__name__)


class GitNexusMCPClient:
    """MCP client for GitNexus — communicates via stdio JSON-RPC.

    Usage:
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
        logger.info("GitNexus MCP client started")

    async def stop(self) -> None:
        """Terminate the MCP subprocess."""
        if self._process:
            self._process.terminate()
            await self._process.wait()
            self._process = None
            logger.info("GitNexus MCP client stopped")

    async def call_tool(self, tool_name: str, arguments: dict) -> list | dict | None:
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

        response_line = await self._process.stdout.readline()
        if not response_line:
            raise ConnectionError("GitNexus MCP closed connection")

        response = json.loads(response_line.decode())

        if "error" in response:
            raise RuntimeError(
                f"MCP error: {response['error'].get('message', 'unknown')}"
            )

        return response.get("result", response)

    def _parse_tool_result(self, result: dict) -> list | dict | None:
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
