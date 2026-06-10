"""Tests for GitNexus MCP client."""

import json
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from shannon_core.code_index.gitnexus_mcp import GitNexusMCPClient


class TestGitNexusMCPClient:
    def test_initial_state(self, tmp_path):
        client = GitNexusMCPClient(tmp_path)
        assert client._request_id == 0
        assert client._process is None

    @pytest.mark.asyncio
    async def test_start_launches_process(self, tmp_path):
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

            await client.start()
            mock_exec.assert_called_once()
            assert client._process is not None
            await client.stop()

    @pytest.mark.asyncio
    async def test_call_tool_sends_request(self, tmp_path):
        client = GitNexusMCPClient(tmp_path)
        with patch("shannon_core.code_index.gitnexus_mcp.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = MagicMock()
            mock_proc.stdin = MagicMock()
            mock_proc.stdin.drain = AsyncMock()
            mock_proc.stdout = AsyncMock()

            # First call: initialize response
            # Second call: tools/call response
            responses = [
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}).encode(),
                json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "[{\"name\": \"ep1\"}]"}]}}).encode(),
            ]
            mock_proc.stdout.readline = AsyncMock(side_effect=responses)
            mock_proc.wait = AsyncMock()
            mock_exec.return_value = mock_proc

            await client.start()
            result = await client.call_tool("cypher", {"query": "MATCH (n) RETURN n"})
            assert result is not None
            await client.stop()

    @pytest.mark.asyncio
    async def test_stop_terminates_process(self, tmp_path):
        client = GitNexusMCPClient(tmp_path)
        mock_proc = MagicMock()
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock()
        client._process = mock_proc

        await client.stop()
        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_noop_when_no_process(self, tmp_path):
        client = GitNexusMCPClient(tmp_path)
        await client.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_send_request_increments_id(self, tmp_path):
        client = GitNexusMCPClient(tmp_path)
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(return_value=json.dumps({
            "jsonrpc": "2.0", "id": 1, "result": {}
        }).encode())
        client._process = mock_proc

        await client._send_request("initialize", {"protocolVersion": "2024-11-05"})
        assert client._request_id == 1

    @pytest.mark.asyncio
    async def test_start_sends_initialized_notification(self, tmp_path):
        """After initialize response, client must send an initialized notification."""
        client = GitNexusMCPClient(tmp_path)
        sent_lines: list[bytes] = []

        with patch("shannon_core.code_index.gitnexus_mcp.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = MagicMock()
            mock_proc.stdin = MagicMock()
            mock_proc.stdin.drain = AsyncMock()

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
