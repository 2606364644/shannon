"""Tests for GitNexus MCP client."""

import json
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from shannon_core.code_index.gitnexus_mcp import GitNexusMCPClient, _parse_md_table


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
        mock_proc.kill.assert_not_called()  # 干净退出不应升级到 SIGKILL

    @pytest.mark.asyncio
    async def test_stop_noop_when_no_process(self, tmp_path):
        client = GitNexusMCPClient(tmp_path)
        await client.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_stop_kills_unresponsive_subprocess(self, tmp_path):
        """子进程不响应 SIGTERM(僵死)时,stop() 必须 SIGKILL 它并在有限时间内返回,
        不能永久阻塞 —— 否则 wait() 永不返回,会把整个 activity 拖到
        start_to_close_timeout 才失败(生产里表现为 10 分钟后 CancelledError)。"""
        client = GitNexusMCPClient(tmp_path)
        killed = asyncio.Event()

        zombie = MagicMock()
        zombie.terminate = MagicMock()
        zombie.kill = MagicMock(side_effect=lambda: killed.set())

        async def _wait():
            # SIGTERM 后仍僵死;直到被 SIGKILL(killed 置位)才退出
            if not killed.is_set():
                await asyncio.Event().wait()
            return 0
        zombie.wait = _wait
        client._process = zombie

        with patch("shannon_core.code_index.gitnexus_mcp.MCP_STOP_TIMEOUT", 0.05):
            # stop 必须在 2s 内自拔返回,不能永久阻塞
            await asyncio.wait_for(client.stop(), timeout=2)

        zombie.terminate.assert_called_once()   # 先尝试 SIGTERM
        zombie.kill.assert_called_once()        # SIGTERM 无效 → 升级 SIGKILL
        assert client._process is None

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


class TestParseMdTable:
    def test_normal_table(self):
        md = "| caller_file | caller_name |\n| --- | --- |\n| app.py | handler |\n| svc.py | get_users |"
        assert _parse_md_table(md) == [
            {"caller_file": "app.py", "caller_name": "handler"},
            {"caller_file": "svc.py", "caller_name": "get_users"},
        ]

    def test_empty_table(self):
        assert _parse_md_table("") == []
        assert _parse_md_table("| a |\n| --- |") == []  # 只有表头+分隔，无数据行

    def test_missing_separator_returns_empty(self):
        # 无 |---| 分隔行 → len(lines) < 3 → []
        assert _parse_md_table("| a |\n| 1 |") == []

    def test_column_mismatch_skipped(self):
        md = "| a | b |\n| --- | --- |\n| 1 | 2 |\n| 3 |"  # 末行列数不齐
        assert _parse_md_table(md) == [{"a": "1", "b": "2"}]
