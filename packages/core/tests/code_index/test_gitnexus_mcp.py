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


class TestParseToolResultRobustness:
    def test_json_with_trailing_hint(self, tmp_path):
        """GitNexus 1.6.7: JSON + trailing 提示文本（json.loads 会 Extra data 失败）。"""
        client = GitNexusMCPClient(tmp_path)
        text = '{"processes": [], "definitions": [{"name": "handler"}]}\nUse context({...}) for details.'
        result = client._parse_tool_result({"content": [{"type": "text", "text": text}]})
        assert isinstance(result, dict)
        assert result["definitions"] == [{"name": "handler"}]

    def test_cypher_markdown_table_decoded_to_rows(self, tmp_path):
        client = GitNexusMCPClient(tmp_path)
        text = '{"markdown": "| caller_file | caller_name |\\n| --- | --- |\\n| app.py | handler |", "row_count": 1}\nhint'
        result = client._parse_tool_result({"content": [{"type": "text", "text": text}]})
        assert result["rows"] == [{"caller_file": "app.py", "caller_name": "handler"}]

    def test_error_text_returns_none_with_warning(self, tmp_path, caplog):
        client = GitNexusMCPClient(tmp_path)
        text = 'Error: Multiple repositories indexed. Specify which one with the "repo" parameter.'
        with caplog.at_level("WARNING", logger="shannon_core.code_index.gitnexus_mcp"):
            result = client._parse_tool_result({"content": [{"type": "text", "text": text}]})
        assert result is None
        assert "non-JSON" in caplog.text

    def test_ambiguous_returns_none_with_warning(self, tmp_path, caplog):
        client = GitNexusMCPClient(tmp_path)
        text = '{"status": "ambiguous", "message": "Found 4 symbols matching"}\nhint'
        with caplog.at_level("WARNING", logger="shannon_core.code_index.gitnexus_mcp"):
            result = client._parse_tool_result({"content": [{"type": "text", "text": text}]})
        assert result is None
        assert "ambiguous" in caplog.text

    def test_empty_result_returns_none(self, tmp_path):
        client = GitNexusMCPClient(tmp_path)
        assert client._parse_tool_result({}) is None


class TestCallToolInjectsRepo:
    @pytest.mark.asyncio
    async def test_injects_repo_path(self, tmp_path):
        """多 repo 索引时 GitNexus 要求 repo 参数；call_tool 必须自动注入 path 形式。"""
        client = GitNexusMCPClient(tmp_path)
        captured: dict = {}

        async def fake_send(method: str, params: dict):
            captured.update(params)
            return {"content": [{"type": "text", "text": "{}"}]}

        client._send_request = fake_send  # bypass subprocess
        await client.call_tool("query", {"query": "entry point"})
        assert captured["arguments"]["repo"] == str(tmp_path)

    @pytest.mark.asyncio
    async def test_does_not_override_explicit_repo(self, tmp_path):
        client = GitNexusMCPClient(tmp_path)
        captured: dict = {}

        async def fake_send(method: str, params: dict):
            captured.update(params)
            return {"content": [{"type": "text", "text": "{}"}]}

        client._send_request = fake_send
        await client.call_tool("query", {"query": "x", "repo": "explicit-name"})
        assert captured["arguments"]["repo"] == "explicit-name"
