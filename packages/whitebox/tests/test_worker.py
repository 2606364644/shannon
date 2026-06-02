import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_whitebox.pipeline.shared import PipelineInput


@pytest.mark.asyncio
async def test_run_scan_persists_session_data(tmp_path):
    """run_scan should create a session.json with repo_path via SessionManager."""
    repo = tmp_path / "target-repo"
    repo.mkdir()

    input = PipelineInput(
        repo_path=str(repo),
        workspace_name="test-ws",
    )

    # Mock Temporal Client and Worker
    mock_result = MagicMock()
    mock_result.status = "completed"

    mock_client = AsyncMock()
    mock_client.execute_workflow = AsyncMock(return_value=mock_result)

    mock_worker = AsyncMock()
    mock_worker.__aenter__ = AsyncMock(return_value=None)
    mock_worker.__aexit__ = AsyncMock(return_value=None)

    with patch("shannon_whitebox.worker.Client.connect", AsyncMock(return_value=mock_client)), \
         patch("shannon_whitebox.worker.Worker", return_value=mock_worker):
        from shannon_whitebox.worker import run_scan
        await run_scan(input, "localhost:7233")

    # Verify session.json was created with repo_path
    session_file = tmp_path / "workspaces" / "test-ws" / "session.json"
    assert session_file.exists(), f"session.json not found at {session_file}"
    data = json.loads(session_file.read_text())
    assert data["repo_path"] == str(repo)
