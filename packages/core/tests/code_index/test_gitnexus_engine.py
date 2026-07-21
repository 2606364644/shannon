import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from supernova_core.code_index.gitnexus_engine import GitNexusEngine, GitNexusError


def _subcommands(mock_run):
    """提取所有 gitnexus 子调用中的子命令名 (cmd[1], 如 'analyze'/'index')。"""
    return [c[0][0][1] for c in mock_run.call_args_list]


def _fake_proc(*, communicate_return=(b"{}", b""), returncode=0, communicate_side_effect=None):
    """构造 _run_cli_async 期望的 fake subprocess proc（asyncio.create_subprocess_exec 返回值）。"""
    proc = MagicMock()
    proc.returncode = returncode
    if communicate_side_effect is not None:
        proc.communicate = AsyncMock(side_effect=communicate_side_effect)
    else:
        proc.communicate = AsyncMock(return_value=communicate_return)
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


class TestGitNexusEngineCLI:
    def test_ensure_indexed_runs_analyze(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            engine.ensure_indexed()
            assert "analyze" in _subcommands(mock_run)

    def test_ensure_indexed_skips_analyze_but_registers_registry_when_indexed(self, tmp_path):
        """Regression: .gitnexus 已存在时跳过 analyze,但仍须 `gitnexus index` 注册进
        全局 registry —— 否则 `gitnexus mcp` 启动后发现 0 个仓, 查询死锁
        (2026-07-08 kol_mapping_service 卡 35min 的真根因)。
        """
        (tmp_path / ".gitnexus").mkdir()
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            engine.ensure_indexed()
            cmds = _subcommands(mock_run)
            assert "analyze" not in cmds
            assert "index" in cmds

    def test_ensure_indexed_registers_registry_after_fresh_analyze(self, tmp_path):
        """fresh analyze 后必须 `gitnexus index` 注册进全局 registry (幂等)。"""
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            engine.ensure_indexed()
            cmds = _subcommands(mock_run)
            assert "analyze" in cmds
            assert "index" in cmds

    def test_ensure_indexed_returns_failure_when_registry_register_fails(self, tmp_path):
        """`gitnexus index` 失败 → success=False (避免 registry 未注册进 MCP 死锁)。"""
        (tmp_path / ".gitnexus").mkdir()
        engine = GitNexusEngine(tmp_path)

        def fake_run(cmd, *a, **kw):
            if cmd[1] == "index":
                return MagicMock(returncode=1, stdout="", stderr="registry write failed")
            return MagicMock(returncode=0, stdout="{}", stderr="")

        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.side_effect = fake_run
            result = engine.ensure_indexed()
            assert result.success is False
            assert result.error_message is not None

    def test_get_context_returns_dict(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        context_data = {"outgoing": {"calls": []}, "incoming": {}, "processes": []}
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=json.dumps(context_data), stderr=""
            )
            result = engine.get_context("my_function")
            assert result == context_data
            cmd = mock_run.call_args[0][0]
            assert "context" in cmd
            assert "--name" in cmd

    def test_cli_error_raises_gitnexus_error(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
            result = engine.ensure_indexed()
            assert result.success is False
            assert "error msg" in result.error_message

    def test_timeout_returns_failed_result(self, tmp_path):
        import subprocess
        engine = GitNexusEngine(tmp_path, timeout=1)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("gitnexus", 1)
            result = engine.ensure_indexed()
            assert result.success is False
            assert "timed out" in result.error_message

    def test_is_available_checks_command(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/gitnexus"
            assert engine.is_available() is True

    def test_is_available_returns_false_when_missing(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.shutil.which") as mock_which:
            mock_which.return_value = None
            assert engine.is_available() is False

    def test_ensure_indexed_force_rebuilds(self, tmp_path):
        """ensure_indexed(force=True) runs analyze --force even when .gitnexus/ exists,
        and still registers the repo into the global registry afterwards."""
        (tmp_path / ".gitnexus").mkdir()  # existing index
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            engine.ensure_indexed(force=True)
            analyze_calls = [c[0][0] for c in mock_run.call_args_list if c[0][0][1] == "analyze"]
            assert analyze_calls, "force 应触发 analyze"
            assert "--force" in analyze_calls[0]
            assert "index" in _subcommands(mock_run)

    def test_ensure_indexed_returns_index_result(self, tmp_path):
        """ensure_indexed returns an IndexResult dataclass."""
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            result = engine.ensure_indexed()
            assert result.success is True
            assert result.is_stale is False

    def test_ensure_indexed_failure_returns_failed_result(self, tmp_path):
        """ensure_indexed returns failed IndexResult on error."""
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
            result = engine.ensure_indexed()
            assert result.success is False
            assert result.error_message is not None

    def test_check_stale_no_git_repo(self, tmp_path):
        """check_stale returns False when no .git exists."""
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            result = engine.check_stale()
            # No git repo → can't determine staleness → assume not stale
            assert result is False

    def test_check_stale_fresh_index(self, tmp_path):
        """check_stale returns False when index is newer than latest commit."""
        import time
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitnexus").mkdir()
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
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
        import time
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitnexus").mkdir()
        engine = GitNexusEngine(tmp_path)
        with patch("supernova_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            # git log returns a very recent timestamp (future)
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=str(int(time.time()) + 10000),  # future timestamp
                stderr="",
            )
            result = engine.check_stale()
            assert result is True


class TestGitNexusEngineAsyncCLI:
    """async 版 (_run_cli_async / ensure_indexed_async): cancel 时能 kill 子进程，
    消除 run_code_index 阻塞 event loop 导致 Ctrl+C 不可取消的根因。"""

    @pytest.mark.asyncio
    async def test_ensure_indexed_async_runs_analyze(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=_fake_proc())) as mock_exec:
            result = await engine.ensure_indexed_async()
        assert result.success is True
        cmds = [c.args[1] for c in mock_exec.call_args_list]
        assert "analyze" in cmds and "index" in cmds

    @pytest.mark.asyncio
    async def test_ensure_indexed_async_skips_analyze_but_registers(self, tmp_path):
        (tmp_path / ".gitnexus").mkdir()
        engine = GitNexusEngine(tmp_path)
        with patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=_fake_proc(communicate_return=(b"", b""))),
        ) as mock_exec:
            await engine.ensure_indexed_async()
        cmds = [c.args[1] for c in mock_exec.call_args_list]
        assert "analyze" not in cmds
        assert "index" in cmds

    @pytest.mark.asyncio
    async def test_run_cli_async_nonzero_returncode_raises(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        proc = _fake_proc(communicate_return=(b"", b"error msg"), returncode=1)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            with pytest.raises(GitNexusError) as ei:
                await engine._run_cli_async("analyze", str(tmp_path))
        assert "error msg" in str(ei.value)

    @pytest.mark.asyncio
    async def test_run_cli_async_timeout_kills_proc(self, tmp_path):
        async def hang():
            await asyncio.sleep(10)
            return (b"", b"")

        engine = GitNexusEngine(tmp_path, timeout=0.01)
        proc = _fake_proc(communicate_side_effect=hang)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            with pytest.raises(GitNexusError) as ei:
                await engine._run_cli_async("analyze", str(tmp_path))
        assert "timed out" in str(ei.value)
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_cli_async_cancel_kills_proc(self, tmp_path):
        """核心：cancel 必杀子进程（消除 300s event loop 阻塞，让 Ctrl+C 可取消）。"""
        engine = GitNexusEngine(tmp_path)
        proc = _fake_proc(communicate_side_effect=asyncio.CancelledError)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            with pytest.raises(asyncio.CancelledError):
                await engine._run_cli_async("analyze", str(tmp_path))
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_cli_async_file_not_found_raises(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError)):
            with pytest.raises(GitNexusError) as ei:
                await engine._run_cli_async("analyze", str(tmp_path))
        msg = str(ei.value).lower()
        assert "not found" in msg or "install" in msg
