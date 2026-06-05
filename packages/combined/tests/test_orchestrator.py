from unittest.mock import AsyncMock, patch

import pytest

from shannon_combined.orchestrator import run_combined_scan


@pytest.mark.asyncio
async def test_run_combined_scan_calls_whitebox_then_blackbox():
    """run_combined_scan should call whitebox run_scan then blackbox run_scan."""
    whitebox_result = {
        "status": "completed",
        "workspace_name": "test-ws-001",
        "deliverables_path": "/repo/workspaces/test-ws-001/.shannon/deliverables",
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
