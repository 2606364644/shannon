import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from shannon_whitebox.worker import poll_workflow_progress


async def test_poll_workflow_progress_queries_and_prints():
    handle = MagicMock()
    progress = MagicMock()
    progress.elapsed_ms = 30000
    progress.current_phase = "recon"
    progress.current_agent = "recon"
    progress.completed_agents = ["preflight"]
    handle.query = AsyncMock(return_value=progress)

    with patch("builtins.print") as mock_print:
        task = asyncio.create_task(poll_workflow_progress(handle, interval_seconds=1))
        await asyncio.sleep(0.2)  # Let it run once
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        mock_print.assert_called()
        output = mock_print.call_args[0][0]
        assert "[30s]" in output
        assert "recon" in output
        assert "Completed: 1" in output


async def test_poll_workflow_progress_handles_query_error():
    handle = MagicMock()
    handle.query = AsyncMock(side_effect=RuntimeError("workflow not found"))

    with patch("builtins.print") as mock_print:
        task = asyncio.create_task(poll_workflow_progress(handle, interval_seconds=1))
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Should not crash, just silently handle the error
        mock_print.assert_not_called()
