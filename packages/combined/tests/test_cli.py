from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from shannon_combined.cli.main import cli


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Shannon" in result.output


def test_scan_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["scan", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.output
    assert "--url" in result.output


def test_scan_calls_orchestrator():
    """scan command should call run_combined_scan and display results."""
    async def fake_combined(*args, **kwargs):
        return {
            "status": "completed",
            "has_whitebox_results": True,
            "found_whitebox_classes": ["injection", "xss"],
            "whitebox_workspace": "test-ws-001",
        }

    with (
        patch("shannon_combined.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_combined.orchestrator.run_combined_scan", side_effect=fake_combined),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["scan", "--repo", "/tmp/repo", "--url", "https://example.com"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "completed" in result.output.lower()
