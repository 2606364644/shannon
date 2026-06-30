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
        return {"container": "running", "healthy": True, "source": "shannon-temporal"}

    with patch("shannon_whitebox.cli.main.get_temporal_status", side_effect=fake_status):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "status"])
    assert result.exit_code == 0
    assert "running" in result.output.lower()
    assert "healthy" in result.output.lower()
    assert "shannon-temporal" in result.output


def test_start_calls_ensure_infra():
    """start command should call ensure_infra before run_scan."""
    async def fake_ensure(*a, **kw):
        pass

    async def fake_run_scan(input, temporal_address, use_rich=False):
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

    async def fake_run_scan(input, temporal_address, use_rich=False):
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

    async def fake_run_scan(input, temporal_address, use_rich=False):
        return {
            "status": "completed",
            "workspace_name": "myapp-20260603-143022",
            "deliverables_path": "/repo/workspaces/myapp-20260603-143022/deliverables",
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


def test_start_shows_results_summary(tmp_path, monkeypatch):
    """Completion output should include a per-class vulnerability count summary."""
    monkeypatch.chdir(tmp_path)

    # Create a workspace with deliverables so compute_deliverables_summary works
    from shannon_core.session import SessionManager
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://myapp.com", "/repo", name="myapp-summary-ws")
    mgr.mark_completed(ws)
    deliverables = ws / "deliverables"
    deliverables.mkdir()
    import json
    (deliverables / "injection_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [
            {"title": "SQLi", "description": "d", "severity": "high", "location": "a.py:1"},
            {"title": "Cmdi", "description": "d", "severity": "medium", "location": "b.py:2"},
        ]}), encoding="utf-8"
    )
    (deliverables / "xss_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [
            {"title": "Reflected XSS", "description": "d", "severity": "medium", "location": "c.py:3"},
        ]}), encoding="utf-8"
    )

    async def fake_ensure(*a, **kw):
        pass

    async def fake_run_scan(input, temporal_address, use_rich=False):
        return {
            "status": "completed",
            "workspace_name": "myapp-summary-ws",
            "deliverables_path": str(deliverables),
            "web_url": "https://myapp.com",
        }

    with (
        patch("shannon_whitebox.cli.main.ensure_infra", side_effect=fake_ensure),
        patch("shannon_whitebox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Results summary" in result.output
    assert "injection" in result.output
    assert "xss" in result.output


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


def test_workspace_delete(tmp_path, monkeypatch):
    """workspace delete should remove the workspace directory."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="wb-del")
    mgr.mark_completed(ws)

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "delete", "wb-del", "--force"])

    assert result.exit_code == 0
    assert "deleted" in result.output.lower()
    assert not ws.exists()


def test_workspace_delete_not_found(tmp_path, monkeypatch):
    """workspace delete with nonexistent name should exit 1."""
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "delete", "nonexistent", "--force"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_workspace_delete_confirms(tmp_path, monkeypatch):
    """workspace delete without --force should ask for confirmation."""
    import json
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="wb-confirm")

    runner = CliRunner()
    # Answer 'y' to the confirmation
    result = runner.invoke(cli, ["workspace", "delete", "wb-confirm"], input="y\n")

    assert result.exit_code == 0
    assert "deleted" in result.output.lower()


def test_workspace_delete_cancelled(tmp_path, monkeypatch):
    """workspace delete confirmation cancelled should not delete."""
    from shannon_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="wb-cancel")

    runner = CliRunner()
    # Answer 'n' to the confirmation
    result = runner.invoke(cli, ["workspace", "delete", "wb-cancel"], input="n\n")

    assert result.exit_code == 0
    assert "cancelled" in result.output.lower()
    assert ws.exists()


def test_start_rejects_fresh_and_rewind_together(monkeypatch):
    from click.testing import CliRunner
    from shannon_whitebox.cli.main import cli
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--repo", "/tmp/fake", "--fresh", "--rewind", "recon"])
    assert result.exit_code != 0
    assert "互斥" in result.output or "mutually" in result.output.lower()


def test_start_rewind_accepted(monkeypatch):
    from click.testing import CliRunner
    from unittest.mock import patch, AsyncMock
    from shannon_whitebox.cli.main import cli

    async def fake_run_scan(input, temporal_address, use_rich=False):
        return {"status": "completed"}
    with patch("shannon_whitebox.cli.main.ensure_infra", AsyncMock()), \
         patch("shannon_whitebox.worker.run_scan", side_effect=fake_run_scan):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake", "--rewind", "recon"])
    assert result.exit_code == 0


def test_start_exits_130_on_cancelled():
    """When the scan is cancelled, the CLI should print a message and exit 130."""
    with (
        patch("shannon_whitebox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_core.runtime.prerequisites.ensure_prerequisite"),
        patch(
            "shannon_whitebox.worker.run_scan",
            new=AsyncMock(return_value={"status": "cancelled"}),
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake"])

    assert result.exit_code == 130
    assert "Scan cancelled." in result.output


def test_start_workflow_failure_shows_friendly_and_exits_1():
    """run_scan 抛 ApplicationFailure → CLI 友好展示 + exit 1，不裸抛 traceback。"""
    from temporalio.exceptions import ApplicationError

    err = ApplicationError(
        "Target http://localhost:4000 resolves to loopback address 127.0.0.1",
        type="InvalidTargetError",
    )
    with (
        patch("shannon_whitebox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_core.runtime.prerequisites.ensure_prerequisite"),
        patch("shannon_whitebox.worker.run_scan", side_effect=err),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake"])

    assert result.exit_code == 1
    assert "InvalidTargetError" in result.output
    assert "loopback" in result.output.lower() or "本机" in result.output
    assert "--debug" in result.output
    assert "Traceback" not in result.output


def test_start_workflow_failure_debug_prints_traceback():
    """--debug 时额外把完整 traceback 打到 stderr。"""
    from temporalio.exceptions import ApplicationError

    err = ApplicationError("boom loopback detail", type="InvalidTargetError")
    with (
        patch("shannon_whitebox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("shannon_core.runtime.prerequisites.ensure_prerequisite"),
        patch("shannon_whitebox.worker.run_scan", side_effect=err),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--repo", "/tmp/fake", "--debug"])

    assert result.exit_code == 1
    assert "Traceback" in result.output


def _patch_start_env(monkeypatch, tmp_path):
    """patch 掉 start 命令里的 infra/prereq/run_scan，返回捕获 PipelineInput 的 dict。"""
    from unittest.mock import AsyncMock

    captured: dict = {}

    async def fake_run_scan(input, *args, **kwargs):
        captured["vuln_classes"] = input.vuln_classes
        return {
            "status": "completed",
            "workspace_name": "ws",
            "deliverables_path": str(tmp_path),
            "web_url": "",
        }

    # cli/main.py:49 是函数内 `from shannon_whitebox.worker import run_scan`，
    # patch worker 模块源头即可被函数内 import 取到。
    monkeypatch.setattr("shannon_whitebox.worker.run_scan", fake_run_scan)
    # ensure_infra 是顶部 import，绑定在 cli.main 命名空间。
    monkeypatch.setattr("shannon_whitebox.cli.main.ensure_infra", AsyncMock(return_value=None))
    # ensure_prerequisite 是函数内 import（line 67），patch 源头模块。
    monkeypatch.setattr(
        "shannon_core.runtime.prerequisites.ensure_prerequisite",
        lambda *a, **k: None,
    )
    return captured


def test_start_vuln_classes_option_sets_pipeline_input(monkeypatch, tmp_path):
    """--vuln-classes 逗号分隔 → PipelineInput.vuln_classes。"""
    captured = _patch_start_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["start", "-r", str(repo), "--vuln-classes", "injection,xss", "--plain"],
    )
    assert result.exit_code == 0, result.output
    assert captured["vuln_classes"] == ["injection", "xss"]


def test_start_vuln_classes_env_sets_pipeline_input(monkeypatch, tmp_path):
    """SHANNON_VULN_CLASSES env → PipelineInput.vuln_classes。"""
    monkeypatch.setenv("SHANNON_VULN_CLASSES", "injection,ssrf")
    captured = _patch_start_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli, ["start", "-r", str(repo), "--plain"])
    assert result.exit_code == 0, result.output
    assert captured["vuln_classes"] == ["injection", "ssrf"]


def test_start_vuln_classes_cli_overrides_env(monkeypatch, tmp_path):
    """CLI > env 优先。"""
    monkeypatch.setenv("SHANNON_VULN_CLASSES", "ssrf")
    captured = _patch_start_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["start", "-r", str(repo), "--vuln-classes", "xss", "--plain"],
    )
    assert result.exit_code == 0, result.output
    assert captured["vuln_classes"] == ["xss"]


def test_start_vuln_classes_invalid_raises_usage_error(monkeypatch, tmp_path):
    """非法 vuln 类 → click.UsageError（exit_code != 0，提示含合法值）。"""
    captured = _patch_start_env(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["start", "-r", str(repo), "--vuln-classes", "injection,foo", "--plain"],
    )
    assert result.exit_code != 0
    assert "foo" in result.output
    # run_scan 不应被调用（解析在构造 PipelineInput 前就失败）
    assert "vuln_classes" not in captured
