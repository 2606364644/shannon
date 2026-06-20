import contextlib

import pytest
from unittest.mock import AsyncMock, patch

from shannon_blackbox.pipeline.shared import BlackboxPipelineInput, BlackboxPipelineState


@pytest.mark.asyncio
async def test_run_scan_uses_dynamic_task_queue():
    """run_scan should generate a unique task queue per scan with shannon-py-bb prefix."""
    input = BlackboxPipelineInput(
        web_url="http://example.com",
        workspace_name="test-bb-tq",
    )

    mock_result = BlackboxPipelineState(status="completed")

    mock_handle = AsyncMock()
    mock_handle.result = AsyncMock(return_value=mock_result)
    mock_handle.query = AsyncMock(side_effect=Exception("no query in test"))

    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)

    captured_task_queue = None

    def capture_worker(**kwargs):
        nonlocal captured_task_queue
        captured_task_queue = kwargs.get("task_queue")
        mock_worker = AsyncMock()
        mock_worker.__aenter__ = AsyncMock(return_value=None)
        mock_worker.__aexit__ = AsyncMock(return_value=None)
        return mock_worker

    with patch("shannon_blackbox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_blackbox.worker.Worker", side_effect=capture_worker), \
         patch("shannon_blackbox.worker.ShutdownController.install"), \
         patch("shannon_blackbox.worker.ShutdownController.uninstall"):
        from shannon_blackbox.worker import run_scan
        await run_scan(input, "localhost:7233")

    assert captured_task_queue is not None
    assert captured_task_queue.startswith("shannon-py-bb-"), f"Expected shannon-py-bb- prefix, got: {captured_task_queue}"
    suffix = captured_task_queue.removeprefix("shannon-py-bb-")
    assert len(suffix) == 8
    int(suffix, 16)  # must be valid hex


@pytest.mark.asyncio
async def test_run_scan_emits_failed_summary_on_workflow_error():
    """On a workflow-level failure (handle.result() raises), run_scan should
    still finalize the dashboard with a failed summary before re-raising."""
    input = BlackboxPipelineInput(
        web_url="http://example.com",
        workspace_name="test-bb-fail",
    )

    boom = RuntimeError("browser-engine-unavailable")

    mock_handle = AsyncMock()
    mock_handle.result = AsyncMock(side_effect=boom)

    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)

    captured_session = {}

    class FakeSession:
        log_workflow_complete = AsyncMock()
        log_error = AsyncMock()

    def capture_worker(**kwargs):
        mock_worker = AsyncMock()
        mock_worker.__aenter__ = AsyncMock(return_value=None)
        mock_worker.__aexit__ = AsyncMock(return_value=None)
        return mock_worker

    fake_session = FakeSession()

    real_run_with_display = __import__(
        "shannon_blackbox.worker", fromlist=["run_with_display"]
    ).run_with_display

    import contextlib

    @contextlib.asynccontextmanager
    async def fake_display(meta, use_rich=False):
        captured_session["session"] = fake_session
        yield fake_session

    with patch("shannon_blackbox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_blackbox.worker.Worker", side_effect=capture_worker), \
         patch("shannon_blackbox.worker.run_with_display", fake_display), \
         patch("shannon_blackbox.worker.ShutdownController.install"), \
         patch("shannon_blackbox.worker.ShutdownController.uninstall"):
        from shannon_blackbox.worker import run_scan
        with pytest.raises(RuntimeError, match="browser-engine-unavailable"):
            await run_scan(input, "localhost:7233")

    fake_session.log_workflow_complete.assert_awaited_once()
    summary = fake_session.log_workflow_complete.await_args.args[0]
    assert summary.status == "failed"
    assert summary.error == "browser-engine-unavailable"


@pytest.mark.asyncio
async def test_run_scan_returns_cancelled_on_scan_cancelled():
    """On user interrupt (ScanCancelled), run_scan returns
    BlackboxPipelineState(status='cancelled') and still clears the audit session."""
    from shannon_core.runtime.scan_runner import ScanCancelled
    import contextlib

    input = BlackboxPipelineInput(
        web_url="http://example.com",
        workspace_name="test-bb-cancel",
    )

    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=AsyncMock())

    def capture_worker(**kwargs):
        mock_worker = AsyncMock()
        mock_worker.__aenter__ = AsyncMock(return_value=None)
        mock_worker.__aexit__ = AsyncMock(return_value=None)
        return mock_worker

    class FakeSession:
        log_workflow_complete = AsyncMock()
        log_error = AsyncMock()

    @contextlib.asynccontextmanager
    async def fake_display(meta, use_rich=False):
        yield FakeSession()

    with (
        patch("shannon_blackbox.worker.Client.connect", AsyncMock(return_value=mock_client)),
        patch("shannon_blackbox.worker.Worker", side_effect=capture_worker),
        patch("shannon_blackbox.worker.run_with_display", fake_display),
        patch("shannon_blackbox.worker.ShutdownController.install"),
        patch("shannon_blackbox.worker.ShutdownController.uninstall"),
        patch(
            "shannon_blackbox.worker.await_workflow_with_shutdown",
            AsyncMock(side_effect=ScanCancelled()),
        ),
        patch("shannon_blackbox.worker.clear_audit_session") as mock_clear,
    ):
        from shannon_blackbox.worker import run_scan
        result = await run_scan(input, "localhost:7233")

    assert result == BlackboxPipelineState(status="cancelled")
    mock_clear.assert_called()  # 清理在 cancel 路径仍执行


@pytest.mark.asyncio
async def test_run_scan_rerun_archives_old_evidence_and_uses_new_id(tmp_path, monkeypatch):
    """--rerun 时：归档旧 evidence + workflow id 带 -rerun- 后缀。"""
    # deliverables 落在 session 维度（workspaces/<session>/deliverables）；
    # resolve_workspaces_dir 读 SHANNON_WORKER_ROOT（非 SHANNON_WORKSPACES_DIR）。
    monkeypatch.setenv("SHANNON_WORKER_ROOT", str(tmp_path / "worker"))
    deliverables = tmp_path / "worker" / "workspaces" / "ws1" / "deliverables"
    deliverables.mkdir(parents=True)
    (deliverables / "injection_exploitation_evidence.md").write_text("# old")
    repo = tmp_path / "repo"

    captured_wf_id = {}
    mock_handle = AsyncMock()
    mock_handle.result = AsyncMock(
        return_value=BlackboxPipelineState(status="completed")
    )
    mock_client = AsyncMock()

    async def capture_start(wf, inp, id, task_queue):
        captured_wf_id["id"] = id
        return mock_handle

    mock_client.start_workflow = capture_start

    def capture_worker(**kwargs):
        mock_worker = AsyncMock()
        mock_worker.__aenter__ = AsyncMock(return_value=None)
        mock_worker.__aexit__ = AsyncMock(return_value=None)
        return mock_worker

    class FakeSession:
        log_workflow_complete = AsyncMock()
        log_workflow_header = AsyncMock()

    @contextlib.asynccontextmanager
    async def fake_display(meta, use_rich=False):
        yield FakeSession()

    inp = BlackboxPipelineInput(
        web_url="https://x.com",
        repo_path=str(repo),
        workspace_name="ws1",
        rerun=True,
    )

    with patch("shannon_blackbox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_blackbox.worker.Worker", side_effect=capture_worker), \
         patch("shannon_blackbox.worker.run_with_display", fake_display), \
         patch("shannon_blackbox.worker.ShutdownController.install"), \
         patch("shannon_blackbox.worker.ShutdownController.uninstall"):
        from shannon_blackbox.worker import run_scan
        await run_scan(inp, "localhost:7233")

    # 归档了旧 evidence
    archive_dirs = list(deliverables.glob(".blackbox-archive/*"))
    assert len(archive_dirs) == 1
    assert (archive_dirs[0] / "injection_exploitation_evidence.md").exists()
    assert not (deliverables / "injection_exploitation_evidence.md").exists()
    # workflow id 带 -rerun- 后缀
    assert "-rerun-" in captured_wf_id["id"]


@pytest.mark.asyncio
async def test_run_scan_self_creates_session_when_workspace_name_empty(tmp_path, monkeypatch):
    """纯黑盒（workspace_name 为空）→ worker 自建 blackbox session 并回填 name。

    spec 决策 6：无白盒 session 可接时黑盒自建一个 session，deliverables 落
    workspaces/<自建session>/deliverables，不报错。
    """
    import json
    import contextlib

    monkeypatch.setenv("SHANNON_WORKER_ROOT", str(tmp_path / "worker"))

    inp = BlackboxPipelineInput(
        web_url="https://standalone.example.com",
        repo_path=None,
        workspace_name=None,  # 纯黑盒
    )

    mock_handle = AsyncMock()
    mock_handle.result = AsyncMock(
        return_value=BlackboxPipelineState(status="completed")
    )
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)

    def capture_worker(**kwargs):
        mock_worker = AsyncMock()
        mock_worker.__aenter__ = AsyncMock(return_value=None)
        mock_worker.__aexit__ = AsyncMock(return_value=None)
        return mock_worker

    class FakeSession:
        log_workflow_complete = AsyncMock()

    @contextlib.asynccontextmanager
    async def fake_display(meta, use_rich=False):
        yield FakeSession()

    with patch("shannon_blackbox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_blackbox.worker.Worker", side_effect=capture_worker), \
         patch("shannon_blackbox.worker.run_with_display", fake_display), \
         patch("shannon_blackbox.worker.ShutdownController.install"), \
         patch("shannon_blackbox.worker.ShutdownController.uninstall"):
        from shannon_blackbox.worker import run_scan
        await run_scan(inp, "localhost:7233")

    # workspace_name 已被回填
    assert inp.workspace_name, "纯黑盒应自建 session 并回填 workspace_name"

    # session 文件存在且 scan_type=blackbox
    ws_dir = tmp_path / "worker" / "workspaces" / inp.workspace_name
    assert ws_dir.exists(), f"自建 session 目录应存在: {ws_dir}"
    session_data = json.loads((ws_dir / "session.json").read_text(encoding="utf-8"))
    assert session_data["scan_type"] == "blackbox"
    assert session_data["web_url"] == "https://standalone.example.com"
