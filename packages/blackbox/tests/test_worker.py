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
         patch("shannon_blackbox.worker.Worker", side_effect=capture_worker):
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
         patch("shannon_blackbox.worker.run_with_display", fake_display):
        from shannon_blackbox.worker import run_scan
        with pytest.raises(RuntimeError, match="browser-engine-unavailable"):
            await run_scan(input, "localhost:7233")

    fake_session.log_workflow_complete.assert_awaited_once()
    summary = fake_session.log_workflow_complete.await_args.args[0]
    assert summary.status == "failed"
    assert summary.error == "browser-engine-unavailable"
