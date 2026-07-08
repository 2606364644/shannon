import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from shannon_core.code_index.gitnexus_engine import GitNexusEngine, GitNexusError


def _subcommands(mock_run):
    """提取所有 gitnexus 子调用中的子命令名 (cmd[1], 如 'analyze'/'index')。"""
    return [c[0][0][1] for c in mock_run.call_args_list]


class TestGitNexusEngineCLI:
    def test_ensure_indexed_runs_analyze(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
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
        with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            engine.ensure_indexed()
            cmds = _subcommands(mock_run)
            assert "analyze" not in cmds
            assert "index" in cmds

    def test_ensure_indexed_registers_registry_after_fresh_analyze(self, tmp_path):
        """fresh analyze 后必须 `gitnexus index` 注册进全局 registry (幂等)。"""
        engine = GitNexusEngine(tmp_path)
        with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
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

        with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.side_effect = fake_run
            result = engine.ensure_indexed()
            assert result.success is False
            assert result.error_message is not None

    def test_get_context_returns_dict(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        context_data = {"outgoing": {"calls": []}, "incoming": {}, "processes": []}
        with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
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
        with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
            result = engine.ensure_indexed()
            assert result.success is False
            assert "error msg" in result.error_message

    def test_timeout_returns_failed_result(self, tmp_path):
        import subprocess
        engine = GitNexusEngine(tmp_path, timeout=1)
        with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("gitnexus", 1)
            result = engine.ensure_indexed()
            assert result.success is False
            assert "timed out" in result.error_message

    def test_is_available_checks_command(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("shannon_core.code_index.gitnexus_engine.shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/gitnexus"
            assert engine.is_available() is True

    def test_is_available_returns_false_when_missing(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("shannon_core.code_index.gitnexus_engine.shutil.which") as mock_which:
            mock_which.return_value = None
            assert engine.is_available() is False

    def test_ensure_indexed_force_rebuilds(self, tmp_path):
        """ensure_indexed(force=True) runs analyze --force even when .gitnexus/ exists,
        and still registers the repo into the global registry afterwards."""
        (tmp_path / ".gitnexus").mkdir()  # existing index
        engine = GitNexusEngine(tmp_path)
        with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            engine.ensure_indexed(force=True)
            analyze_calls = [c[0][0] for c in mock_run.call_args_list if c[0][0][1] == "analyze"]
            assert analyze_calls, "force 应触发 analyze"
            assert "--force" in analyze_calls[0]
            assert "index" in _subcommands(mock_run)

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
        import time
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitnexus").mkdir()
        engine = GitNexusEngine(tmp_path)
        with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
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
        with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            # git log returns a very recent timestamp (future)
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=str(int(time.time()) + 10000),  # future timestamp
                stderr="",
            )
            result = engine.check_stale()
            assert result is True
