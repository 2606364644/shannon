from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from shannon_blackbox.cli.main import cli
from shannon_blackbox.pipeline.shared import BlackboxPipelineInput, BlackboxPipelineState


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Shannon Black-Box Scanner" in result.output


def test_start_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--help"])
    assert result.exit_code == 0
    assert "--url" in result.output


def test_start_help_shows_repo_option():
    """Blackbox start --help should list --repo."""
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.output or "-r" in result.output


def test_start_wires_repo_param():
    """--repo arg should be resolved to an absolute path and passed to run_scan."""
    fake_repo = "/fake/repo"
    expected_repo_path = str(Path(fake_repo).resolve())

    captured_input: BlackboxPipelineInput | None = None

    async def fake_run_scan(input: BlackboxPipelineInput, temporal_address: str) -> BlackboxPipelineState:
        nonlocal captured_input
        captured_input = input
        return BlackboxPipelineState(status="completed")

    with patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com", "--repo", fake_repo])

    assert result.exit_code == 0, f"CLI exited with {result.exit_code}: {result.output}"
    assert captured_input is not None, "run_scan was not called"
    assert isinstance(captured_input, BlackboxPipelineInput)
    assert captured_input.repo_path == expected_repo_path


def test_workspaces_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["workspaces", "--help"])
    assert result.exit_code == 0


def test_logs_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["logs", "--help"])
    assert result.exit_code == 0
