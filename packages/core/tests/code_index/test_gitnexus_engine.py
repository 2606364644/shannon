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

    def test_cli_error_raises_gitnexus_error(self, tmp_path):
        engine = GitNexusEngine(tmp_path)
        with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
            with pytest.raises(GitNexusError, match="gitnexus analyze failed"):
                engine.ensure_indexed()

    def test_timeout_raises_timeout(self, tmp_path):
        import subprocess
        engine = GitNexusEngine(tmp_path, timeout=1)
        with patch("shannon_core.code_index.gitnexus_engine.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("gitnexus", 1)
            with pytest.raises(GitNexusError, match="timed out"):
                engine.ensure_indexed()

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
