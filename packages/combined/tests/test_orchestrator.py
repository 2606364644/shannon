from unittest.mock import AsyncMock, patch

import pytest

from shannon_blackbox.pipeline.shared import BlackboxPipelineState
from shannon_combined.orchestrator import run_combined_scan


@pytest.mark.asyncio
async def test_run_combined_scan_calls_whitebox_then_blackbox():
    """run_combined_scan should call whitebox run_scan then blackbox run_scan."""
    whitebox_result = {
        "status": "completed",
        "workspace_name": "test-ws-001",
        "deliverables_path": "/repo/workspaces/test-ws-001/deliverables",
        "web_url": "https://example.com",
    }

    blackbox_state = {
        "status": "completed",
        "has_whitebox_results": True,
        "found_whitebox_classes": ["injection", "xss"],
    }

    with (
        patch("shannon_combined.orchestrator.run_whitebox_scan", new_callable=AsyncMock, return_value=whitebox_result) as mock_wb,
        patch("shannon_combined.orchestrator.run_blackbox_scan", new_callable=AsyncMock, return_value=blackbox_state) as mock_bb,
    ):
        result = await run_combined_scan(
            repo_path="/data/repos/myrepo",
            url="https://example.com",
            temporal_address="localhost:7233",
        )

    mock_wb.assert_called_once()
    mock_bb.assert_called_once()
    assert result["status"] == "completed"
    assert result["whitebox_workspace"] == "test-ws-001"


@pytest.mark.asyncio
async def test_run_combined_scan_stops_on_whitebox_failure():
    """If whitebox fails, blackbox should not be called."""
    whitebox_result = {"status": "failed", "error": "repo not found"}

    with (
        patch("shannon_combined.orchestrator.run_whitebox_scan", new_callable=AsyncMock, return_value=whitebox_result) as mock_wb,
        patch("shannon_combined.orchestrator.run_blackbox_scan", new_callable=AsyncMock) as mock_bb,
    ):
        result = await run_combined_scan(
            repo_path="/data/repos/myrepo",
            url="https://example.com",
            temporal_address="localhost:7233",
        )

    mock_wb.assert_called_once()
    mock_bb.assert_not_called()
    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_whitebox_cancelled_short_circuits_before_blackbox():
    """If whitebox is cancelled, the combined scan returns cancelled and
    does not run blackbox."""
    with (
        patch("shannon_combined.orchestrator.run_whitebox_scan", new_callable=AsyncMock, return_value={"status": "cancelled"}) as mock_wb,
        patch("shannon_combined.orchestrator.run_blackbox_scan", new_callable=AsyncMock) as mock_bb,
    ):
        result = await run_combined_scan(
            repo_path="/data/repos/myrepo",
            url="https://example.com",
            temporal_address="localhost:7233",
        )

    mock_wb.assert_called_once()
    mock_bb.assert_not_called()  # 关键：blackbox 阶段未执行
    assert result == {"status": "cancelled", "phase": "whitebox"}


@pytest.mark.asyncio
async def test_combined_wires_env_concurrency_to_both_inputs(monkeypatch):
    """SHANNON_MAX_CONCURRENT=2 → both wb_input and bb_input get max_concurrent=2."""
    monkeypatch.setenv("SHANNON_MAX_CONCURRENT", "2")
    whitebox_result = {"status": "completed", "workspace_name": "ws-1"}
    blackbox_state = BlackboxPipelineState(status="completed")

    with (
        patch("shannon_combined.orchestrator.run_whitebox_scan", new_callable=AsyncMock, return_value=whitebox_result) as mock_wb,
        patch("shannon_combined.orchestrator.run_blackbox_scan", new_callable=AsyncMock, return_value=blackbox_state) as mock_bb,
    ):
        await run_combined_scan(repo_path="/fake/repo", url="http://example.com")

    wb_input = mock_wb.call_args.args[0]
    bb_input = mock_bb.call_args.args[0]
    assert wb_input.max_concurrent == 2
    assert bb_input.max_concurrent == 2
