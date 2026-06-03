from pathlib import Path
from unittest.mock import AsyncMock, patch

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

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com", "--repo", fake_repo])

    assert result.exit_code == 0, f"CLI exited with {result.exit_code}: {result.output}"
    assert captured_input is not None, "run_scan was not called"
    assert isinstance(captured_input, BlackboxPipelineInput)
    assert captured_input.repo_path == expected_repo_path


def test_start_shows_whitebox_completion_message():
    """When whitebox results are found, completion message should mention them."""
    async def fake_run_scan(input, temporal_address):
        return BlackboxPipelineState(
            status="completed",
            has_whitebox_results=True,
            found_whitebox_classes=["injection", "xss"],
        )

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"])

    assert result.exit_code == 0
    assert "leveraged whitebox results" in result.output
    assert "injection" in result.output


def test_start_shows_standalone_completion_message():
    """When no whitebox results, completion message should say standalone."""
    async def fake_run_scan(input, temporal_address):
        return BlackboxPipelineState(status="completed")

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"])

    assert result.exit_code == 0
    assert "standalone" in result.output


def test_start_shows_error_on_failure():
    """When scan fails, CLI should show error and exit 1."""
    async def fake_run_scan(input, temporal_address):
        return BlackboxPipelineState(status="failed", errors=["something broke"])

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"])

    assert result.exit_code == 1
    assert "something broke" in result.output


def test_workspaces_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["workspaces", "--help"])
    assert result.exit_code == 0


def test_logs_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["logs", "--help"])
    assert result.exit_code == 0


def test_infra_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["infra", "--help"])
    assert result.exit_code == 0
    assert "Manage Temporal infrastructure" in result.output


def test_infra_up():
    with (
        patch("shannon_blackbox.cli.main.start_temporal"),
        patch("shannon_blackbox.cli.main.is_temporal_ready", new_callable=AsyncMock, return_value=True),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "up"])
    assert result.exit_code == 0
    assert "ready" in result.output.lower()


def test_infra_down():
    with patch("shannon_blackbox.cli.main.stop_temporal"):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "down"])
    assert result.exit_code == 0
    assert "stopped" in result.output.lower()


def test_infra_status():
    async def fake_status(**kwargs):
        return {"container": "running", "healthy": True}

    with patch("shannon_blackbox.cli.main.get_temporal_status", side_effect=fake_status):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "status"])
    assert result.exit_code == 0
    assert "running" in result.output.lower()
    assert "healthy" in result.output.lower()


def test_start_help_shows_latest_option():
    """Blackbox start --help should list --latest."""
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--help"])
    assert result.exit_code == 0
    assert "--latest" in result.output


def test_start_calls_ensure_infra():
    """start command should call ensure_infra before run_scan."""
    async def fake_ensure(*a, **kw):
        pass

    async def fake_run_scan(input, temporal_address):
        return BlackboxPipelineState(status="completed")

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", side_effect=fake_ensure) as mock_ensure,
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    mock_ensure.assert_called_once()


def test_latest_resolves_to_workspace(tmp_path, monkeypatch):
    """--latest should resolve to the most recent whitebox workspace with deliverables."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://myapp.com", "/repo", name="myapp-wb", scan_type="whitebox")
    mgr.mark_completed(ws)
    deliverables = ws / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
    )

    captured_input = None

    async def fake_run_scan(input, temporal_address):
        nonlocal captured_input
        captured_input = input
        return BlackboxPipelineState(status="completed")

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com", "--latest"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert captured_input is not None
    assert captured_input.workspace_name == "myapp-wb"
    assert "Found white-box results" in result.output


def test_latest_no_workspaces(tmp_path, monkeypatch):
    """--latest with no workspaces should print error and exit 1."""
    monkeypatch.chdir(tmp_path)

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com", "--latest"])

    assert result.exit_code == 1
    assert "No white-box workspaces found" in result.output


def test_w_takes_precedence_over_latest(tmp_path, monkeypatch):
    """When both -w and --latest are given, -w wins."""
    monkeypatch.chdir(tmp_path)

    captured_input = None

    async def fake_run_scan(input, temporal_address):
        nonlocal captured_input
        captured_input = input
        return BlackboxPipelineState(status="completed")

    with (
        patch("shannon_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com", "-w", "my-ws", "--latest"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert captured_input.workspace_name == "my-ws"
