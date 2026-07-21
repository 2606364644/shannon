from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from supernova_blackbox.cli.main import cli
from supernova_blackbox.pipeline.shared import BlackboxPipelineInput, BlackboxPipelineState


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Supernova Black-Box Scanner" in result.output


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

    async def fake_run_scan(input: BlackboxPipelineInput, temporal_address: str, use_rich: bool = False) -> BlackboxPipelineState:
        nonlocal captured_input
        captured_input = input
        return BlackboxPipelineState(status="completed")

    with (
        patch("supernova_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("supernova_blackbox.cli.main.find_latest_workspace", return_value=None),
        patch("supernova_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com", "--repo", fake_repo])

    assert result.exit_code == 0, f"CLI exited with {result.exit_code}: {result.output}"
    assert captured_input is not None, "run_scan was not called"
    assert isinstance(captured_input, BlackboxPipelineInput)
    assert captured_input.repo_path == expected_repo_path


def test_start_shows_whitebox_completion_message():
    """When whitebox results are found, completion message should mention them."""
    async def fake_run_scan(input, temporal_address, use_rich=False):
        return BlackboxPipelineState(
            status="completed",
            has_whitebox_results=True,
            found_whitebox_classes=["injection", "xss"],
        )

    with (
        patch("supernova_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("supernova_blackbox.cli.main.find_latest_workspace", return_value=None),
        patch("supernova_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"])

    assert result.exit_code == 0
    assert "leveraged whitebox results" in result.output
    assert "injection" in result.output


def test_start_shows_standalone_completion_message():
    """When no whitebox results, completion message should say standalone."""
    async def fake_run_scan(input, temporal_address, use_rich=False):
        return BlackboxPipelineState(status="completed")

    with (
        patch("supernova_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("supernova_blackbox.cli.main.find_latest_workspace", return_value=None),
        patch("supernova_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"])

    assert result.exit_code == 0
    assert "standalone" in result.output


def test_start_shows_error_on_failure():
    """When scan fails, CLI should show error and exit 1."""
    async def fake_run_scan(input, temporal_address, use_rich=False):
        return BlackboxPipelineState(status="failed", errors=["something broke"])

    with (
        patch("supernova_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("supernova_blackbox.cli.main.find_latest_workspace", return_value=None),
        patch("supernova_blackbox.worker.run_scan", side_effect=fake_run_scan),
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
        patch("supernova_blackbox.cli.main.start_temporal"),
        patch("supernova_blackbox.cli.main.is_temporal_ready", new_callable=AsyncMock, return_value=True),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "up"])
    assert result.exit_code == 0
    assert "ready" in result.output.lower()


def test_infra_down():
    with patch("supernova_blackbox.cli.main.stop_temporal"):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "down"])
    assert result.exit_code == 0
    assert "stopped" in result.output.lower()


def test_infra_status():
    async def fake_status(**kwargs):
        return {"container": "running", "healthy": True, "source": "shannon-temporal"}

    with patch("supernova_blackbox.cli.main.get_temporal_status", side_effect=fake_status):
        runner = CliRunner()
        result = runner.invoke(cli, ["infra", "status"])
    assert result.exit_code == 0
    assert "running" in result.output.lower()
    assert "healthy" in result.output.lower()
    assert "shannon-temporal" in result.output


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

    async def fake_run_scan(input, temporal_address, use_rich=False):
        return BlackboxPipelineState(status="completed")

    with (
        patch("supernova_blackbox.cli.main.ensure_infra", side_effect=fake_ensure) as mock_ensure,
        patch("supernova_blackbox.cli.main.find_latest_workspace", return_value=None),
        patch("supernova_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    mock_ensure.assert_called_once()


def test_latest_resolves_to_workspace(tmp_path, monkeypatch):
    """--latest should resolve to the most recent whitebox workspace with deliverables."""
    import json
    from supernova_core.session import SessionManager

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

    async def fake_run_scan(input, temporal_address, use_rich=False):
        nonlocal captured_input
        captured_input = input
        return BlackboxPipelineState(status="completed")

    env_patch = _patch_env_profile()
    with (
        env_patch[0], env_patch[1],
        patch("supernova_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("supernova_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com", "--latest"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert captured_input is not None
    assert captured_input.workspace_name == "myapp-wb"
    assert "Found white-box results" in result.output


def _patch_env_profile():
    """隔离 .env / profile 校验（这些 workspace 解析测试不关心引擎配置）。

    CLI 的 cli() group callback 调 load_env() + validate_active_profile()；
    monkeypatch.chdir 后 tmp_path 下无 .env，故需 patch 掉这两步。
    """
    return (
        patch("supernova_blackbox.cli.main.load_env", return_value="test"),
        patch("supernova_blackbox.cli.main.validate_active_profile"),
    )


def test_start_defaults_to_latest_when_no_flags(tmp_path, monkeypatch):
    """不传 -w/--latest 时默认复用最近白盒 workspace（软默认 latest）。"""
    import json
    from supernova_core.session import SessionManager

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

    async def fake_run_scan(input, temporal_address, use_rich=False):
        nonlocal captured_input
        captured_input = input
        return BlackboxPipelineState(status="completed")

    env_patch = _patch_env_profile()
    with (
        env_patch[0], env_patch[1],
        patch("supernova_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("supernova_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        # 注意：不传 --latest 也不传 -w，应走软默认 latest
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert captured_input is not None, "run_scan was not called"
    assert captured_input.workspace_name == "myapp-wb", (
        f"默认应解析到最近白盒 workspace，实得 {captured_input.workspace_name!r}"
    )
    # 应该走 latest 路径（非交互 URL 匹配）
    assert "Found white-box results" in result.output


def test_start_defaults_to_standalone_when_no_whitebox(tmp_path, monkeypatch):
    """无白盒 workspace 时，默认不传 flag 应退化为 standalone（不报错）。"""
    monkeypatch.chdir(tmp_path)

    captured_input = None

    async def fake_run_scan(input, temporal_address, use_rich=False):
        nonlocal captured_input
        captured_input = input
        return BlackboxPipelineState(status="completed")

    env_patch = _patch_env_profile()
    with (
        env_patch[0], env_patch[1],
        patch("supernova_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("supernova_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert captured_input is not None
    # 软默认：找不到白盒 → standalone，workspace_name 为 None（worker 自建 session）
    assert captured_input.workspace_name is None
    assert "standalone" in result.output.lower()


def test_latest_no_workspaces(tmp_path, monkeypatch):
    """--latest with no workspaces should print error and exit 1."""
    monkeypatch.chdir(tmp_path)

    env_patch = _patch_env_profile()
    with (
        env_patch[0], env_patch[1],
        patch("supernova_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com", "--latest"])

    assert result.exit_code == 1
    assert "No white-box workspaces found" in result.output


def test_w_takes_precedence_over_latest(tmp_path, monkeypatch):
    """When both -w and --latest are given, -w wins."""
    monkeypatch.chdir(tmp_path)

    captured_input = None

    async def fake_run_scan(input, temporal_address, use_rich=False):
        nonlocal captured_input
        captured_input = input
        return BlackboxPipelineState(status="completed")

    env_patch = _patch_env_profile()
    with (
        env_patch[0], env_patch[1],
        patch("supernova_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("supernova_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com", "-w", "my-ws", "--latest"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert captured_input.workspace_name == "my-ws"


def test_latest_and_w_conflict_warns(tmp_path, monkeypatch):
    """When both --latest and -w are specified, a warning should be printed."""
    monkeypatch.chdir(tmp_path)

    captured_input = None

    async def fake_run_scan(input, temporal_address, use_rich=False):
        nonlocal captured_input
        captured_input = input
        return BlackboxPipelineState(status="completed")

    env_patch = _patch_env_profile()
    with (
        env_patch[0], env_patch[1],
        patch("supernova_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("supernova_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://myapp.com", "-w", "my-ws", "--latest"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert captured_input.workspace_name == "my-ws"
    assert "-w takes precedence" in result.output


def test_workspaces_grouped_by_scan_type(tmp_path, monkeypatch):
    """workspaces command should group output by scan_type."""
    import json
    from supernova_core.session import SessionManager

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


def test_workspace_show(tmp_path, monkeypatch):
    """workspace show should display detailed workspace info."""
    import json
    from supernova_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://myapp.com", "/repo", name="myapp-bb", scan_type="blackbox")
    mgr.set_parent_workspace(ws, "wb-parent")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "show", "myapp-bb"])

    assert result.exit_code == 0
    assert "myapp-bb" in result.output
    assert "blackbox" in result.output
    assert "wb-parent" in result.output


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


def test_workspace_delete(tmp_path, monkeypatch):
    """workspace delete should remove the workspace directory."""
    import json
    from supernova_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="bb-del", scan_type="blackbox")
    mgr.mark_completed(ws)

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "delete", "bb-del", "--force"])

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
    from supernova_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="bb-confirm", scan_type="blackbox")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "delete", "bb-confirm"], input="y\n")

    assert result.exit_code == 0
    assert "deleted" in result.output.lower()


def test_workspace_delete_cancelled(tmp_path, monkeypatch):
    """workspace delete confirmation cancelled should not delete."""
    from supernova_core.session import SessionManager

    monkeypatch.chdir(tmp_path)

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="bb-cancel", scan_type="blackbox")

    runner = CliRunner()
    result = runner.invoke(cli, ["workspace", "delete", "bb-cancel"], input="n\n")

    assert result.exit_code == 0
    assert "cancelled" in result.output.lower()
    assert ws.exists()


def test_start_exits_130_on_cancelled():
    """When the scan is cancelled, the CLI should print a message and exit 130."""
    async def fake_run_scan(input, temporal_address, use_rich=False):
        return BlackboxPipelineState(status="cancelled")

    with (
        patch("supernova_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("supernova_core.runtime.prerequisites.ensure_prerequisite"),
        # 无白盒 workspace → standalone，避免 workspace 解析干扰取消行为断言
        patch("supernova_blackbox.cli.main.find_latest_workspace", return_value=None),
        patch("supernova_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"])

    assert result.exit_code == 130
    assert "Scan cancelled." in result.output


def test_start_informs_when_blackbox_already_ran(tmp_path, monkeypatch):
    """默认（非 --rerun）检测到已跑过黑盒 → 告知、不调 run_scan。"""
    from click.testing import CliRunner
    from unittest.mock import patch, AsyncMock
    from supernova_blackbox.cli.main import cli

    # deliverables 落在 session 维度（workspaces/<session>/deliverables）
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path / "worker"))
    deliverables = tmp_path / "worker" / "workspaces" / "ws1" / "deliverables"
    deliverables.mkdir(parents=True)
    (deliverables / "injection_exploitation_evidence.md").write_text("# done")
    repo = tmp_path / "repo"

    run_scan_called = []
    async def fake_run_scan(input, temporal_address, use_rich=False):
        run_scan_called.append(True)
        return BlackboxPipelineState(status="completed")

    with patch("supernova_blackbox.cli.main.ensure_infra", AsyncMock()), \
         patch("supernova_blackbox.worker.run_scan", side_effect=fake_run_scan):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://x.com", "-r", str(repo), "-w", "ws1"])

    assert result.exit_code == 0
    assert "已跑过" in result.output or "already" in result.output.lower()
    assert run_scan_called == []  # 没调 run_scan


def test_start_rerun_bypasses_idempotency(tmp_path, monkeypatch):
    """--rerun 跳过幂等检测，正常调 run_scan。"""
    from click.testing import CliRunner
    from unittest.mock import patch, AsyncMock
    from supernova_blackbox.cli.main import cli

    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path / "worker"))
    deliverables = tmp_path / "worker" / "workspaces" / "ws1" / "deliverables"
    deliverables.mkdir(parents=True)
    (deliverables / "injection_exploitation_evidence.md").write_text("# old")
    repo = tmp_path / "repo"

    run_scan_called = []
    captured = {}
    async def fake_run_scan(input, temporal_address, use_rich=False):
        run_scan_called.append(True)
        captured["rerun"] = input.rerun
        return BlackboxPipelineState(status="completed")

    with patch("supernova_blackbox.cli.main.ensure_infra", AsyncMock()), \
         patch("supernova_blackbox.worker.run_scan", side_effect=fake_run_scan):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "https://x.com", "-r", str(repo), "-w", "ws1", "--rerun"])

    assert result.exit_code == 0
    assert run_scan_called == [True]
    assert captured["rerun"] is True


def _capture_input(monkeypatch, extra_args, env_value=None):
    """Invoke `blackbox start` with run_scan mocked; return captured BlackboxPipelineInput."""
    if env_value is None:
        monkeypatch.delenv("SUPERNOVA_MAX_CONCURRENT", raising=False)
    else:
        monkeypatch.setenv("SUPERNOVA_MAX_CONCURRENT", env_value)

    captured: list[BlackboxPipelineInput] = []

    async def fake_run_scan(input, temporal_address, use_rich=False):
        captured.append(input)
        return BlackboxPipelineState(status="completed")

    with (
        patch("supernova_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("supernova_blackbox.cli.main.find_latest_workspace", return_value=None),
        patch("supernova_blackbox.worker.run_scan", side_effect=fake_run_scan),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://example.com"] + extra_args)
    assert result.exit_code == 0, f"CLI failed: {result.output}"
    return captured[0]


def test_max_concurrent_default_from_env(monkeypatch):
    """SUPERNOVA_MAX_CONCURRENT=2 → BlackboxPipelineInput.max_concurrent == 2 (no CLI flag)."""
    input = _capture_input(monkeypatch, extra_args=[], env_value="2")
    assert input.max_concurrent == 2


def test_max_concurrent_cli_overrides_env(monkeypatch):
    """--max-concurrent 5 overrides SUPERNOVA_MAX_CONCURRENT=2."""
    input = _capture_input(monkeypatch, extra_args=["--max-concurrent", "5"], env_value="2")
    assert input.max_concurrent == 5


def test_max_concurrent_default_3_when_unset(monkeypatch):
    """No env, no flag → default 3."""
    input = _capture_input(monkeypatch, extra_args=[], env_value=None)
    assert input.max_concurrent == 3


def test_start_workflow_failure_shows_friendly_and_exits_1(tmp_path, monkeypatch):
    """run_scan 抛 ApplicationFailure → CLI 友好展示 + exit 1，不裸抛 traceback。"""
    from temporalio.exceptions import ApplicationError

    err = ApplicationError(
        "Target http://localhost:4000 resolves to loopback address 127.0.0.1",
        type="InvalidTargetError",
    )
    monkeypatch.chdir(tmp_path)
    ep = _patch_env_profile()
    with (
        ep[0], ep[1],
        patch("supernova_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("supernova_blackbox.cli.main.find_latest_workspace", return_value=None),
        patch("supernova_core.runtime.prerequisites.ensure_prerequisite"),
        patch("supernova_blackbox.worker.run_scan", side_effect=err),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://localhost:4000"])

    assert result.exit_code == 1
    assert "InvalidTargetError" in result.output
    assert "loopback" in result.output.lower() or "本机" in result.output
    assert "--debug" in result.output
    assert "Traceback" not in result.output  # 默认不裸抛堆栈


def test_start_workflow_failure_debug_prints_traceback(tmp_path, monkeypatch):
    """--debug 时除友好串外，额外把完整 traceback 打到 stderr。"""
    from temporalio.exceptions import ApplicationError

    err = ApplicationError("boom loopback detail", type="InvalidTargetError")
    monkeypatch.chdir(tmp_path)
    ep = _patch_env_profile()
    with (
        ep[0], ep[1],
        patch("supernova_blackbox.cli.main.ensure_infra", new_callable=AsyncMock),
        patch("supernova_blackbox.cli.main.find_latest_workspace", return_value=None),
        patch("supernova_core.runtime.prerequisites.ensure_prerequisite"),
        patch("supernova_blackbox.worker.run_scan", side_effect=err),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["start", "--url", "http://localhost:4000", "--debug"])

    assert result.exit_code == 1
    assert "Traceback" in result.output  # --debug 打了堆栈（CliRunner mix_stderr）


def test_start_help_shows_debug_option():
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "--help"])
    assert result.exit_code == 0
    assert "--debug" in result.output
