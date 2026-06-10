import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from shannon_core.code_index.gitnexus_engine import GitNexusEngine, GitNexusError


class TestGitNexusEngineCLI:
    def test_ensure_indexed_runs_analyze(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            engine.ensure_indexed()
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "gitnexus"
            assert cmd[1] == "analyze"

    def test_ensure_indexed_skips_if_already_indexed(self, tmp_path):
        (tmp_path / ".gitnexus").mkdir()
        engine = GitNexusEngine(tmp_path)
        with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            engine.ensure_indexed()
            mock_run.assert_not_called()

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

    def test_cli_error_returns_failed_result(self, tmp_path):
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
