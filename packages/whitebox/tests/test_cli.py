from unittest.mock import AsyncMock, patch

from click.testing import CliRunner
from shannon_whitebox.cli.main import cli


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Shannon White-Box Scanner" in result.output


def test_start_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--help"])
    assert result.exit_code == 0
    assert "--repo" in result.output


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
        patch("shannon_whitebox.cli.main.start_temporal"),
        patch("shannon_whitebox.cli.main.is_temporal_ready", new_callable=AsyncMock, return_value=True),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "up"])
    assert result.exit_code == 0
    assert "ready" in result.output.lower()


def test_infra_down():
    with patch("shannon_whitebox.cli.main.stop_temporal"):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "down"])
    assert result.exit_code == 0
    assert "stopped" in result.output.lower()


def test_infra_status():
    async def fake_status(**kwargs):
        return {"container": "running", "healthy": True}

    with patch("shannon_whitebox.cli.main.get_temporal_status", side_effect=fake_status):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "status"])
    assert result.exit_code == 0
    assert "running" in result.output.lower()
    assert "healthy" in result.output.lower()


def test_start_calls_ensure_infra():
    """start command should call ensure_infra before run_scan."""
    async def fake_ensure(*a, **kw):
        pass

    async def fake_run_scan(input, temporal_address):
        return {"status": "completed"}

    with (
        patch("shannon_whitebox.cli.main.ensure_infra", side_effect=fake_ensure) as mock_ensure,
        patch("shannon_whitebox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    mock_ensure.assert_called_once()


def test_start_shows_workspace_and_next_steps(tmp_path, monkeypatch):
    """Completion output should show workspace name, deliverables path, and next-step commands."""
    monkeypatch.chdir(tmp_path)

    async def fake_ensure(*a, **kw):
        pass

    async def fake_run_scan(input, temporal_address):
        return {"status": "completed", "workspace_name": "myapp-20260603-143022"}

    with (
        patch("shannon_whitebox.cli.main.ensure_infra", side_effect=fake_ensure),
        patch("shannon_whitebox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Workspace:" in result.output
    assert "Next steps:" in result.output
    assert "shannon-blackbox start" in result.output
    assert "--latest" in result.output


def test_start_shows_deliverables_path(tmp_path, monkeypatch):
    """Completion output should show deliverables path when returned by worker."""
    monkeypatch.chdir(tmp_path)

    async def fake_ensure(*a, **kw):
        pass

    async def fake_run_scan(input, temporal_address):
        return {
            "status": "completed",
            "workspace_name": "myapp-20260603-143022",
            "deliverables_path": "/repo/workspaces/myapp-20260603-143022/.shannon/deliverables",
            "web_url": "https://example.com",
        }

    with (
        patch("shannon_whitebox.cli.main.ensure_infra", side_effect=fake_ensure),
        patch("shannon_whitebox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "myapp-20260603-143022" in result.output
    assert "deliverables" in result.output


def test_workspaces_grouped_by_scan_type(tmp_path, monkeypatch):
    """workspaces command should group output by scan_type."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    wb = mgr.create_workspace("https://myapp.com", "/repo", name="wb-1", scan_type="whitebox")
    mgr.mark_completed(wb)
    deliverables = wb / "deliverables"
    deliverables.mkdir()
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
    )

    bb = mgr.create_workspace("https://myapp.com", "/repo", name="bb-1", scan_type="blackbox")
    mgr.set_parent_workspace(bb, "wb-1")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspaces"])

    assert result.exit_code == 0
    assert "White-box workspaces:" in result.output
    assert "Black-box workspaces:" in result.output
    assert "wb-1" in result.output
    assert "bb-1" in result.output


def test_workspace_show(tmp_path, monkeypatch):
    """workspace show should display detailed workspace info."""
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
    (deliverables / "executive_summary.md").write_text("# Summary", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "show", "myapp-wb"])

    assert result.exit_code == 0
    assert "myapp-wb" in result.output
    assert "whitebox" in result.output
    assert "https://myapp.com" in result.output
    assert "injection_exploitation_queue.json" in result.output
    assert "executive_summary.md" in result.output


def test_workspace_show_not_found(tmp_path, monkeypatch):
    """workspace show with nonexistent name should exit 1."""
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "show", "nonexistent"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_logs_command_accepts_follow_flag(tmp_path, monkeypatch):
    """The logs command should accept a --follow flag."""
    runner = CliRunner()
    # Create a workspace with a workflow.log
    ws = tmp_path / "workspaces" / "test-ws"
    ws.mkdir(parents=True)
    (ws / "workflow.log").write_text("line 1\n")
    monkeypatch.chdir(tmp_path)
    # Just test that --follow is accepted as an option (it will error on missing watchdog setup in test, but the flag should parse)
    result = runner.invoke(cli, ["logs", "test-ws", "--follow"])
    # We expect it to either work or fail at runtime, not at argument parsing
    assert "--follow" not in (result.output or "")  # --follow shouldn't appear as an error about unknown option


def test_logs_command_shows_content_without_follow(tmp_path, monkeypatch):
    """Without --follow, logs command should cat the file."""
    runner = CliRunner()
    # Create workspaces directory
    ws = tmp_path / "workspaces" / "test-ws"
    ws.mkdir(parents=True)
    (ws / "workflow.log").write_text("hello from log\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["logs", "test-ws"])
    assert result.exit_code == 0
    assert "hello from log" in result.output
