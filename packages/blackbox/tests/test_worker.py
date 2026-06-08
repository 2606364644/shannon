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
