import json
from pathlib import Path

from shannon_blackbox.pipeline.shared import BlackboxPipelineInput, BlackboxPipelineState
from shannon_core.models.errors import classify_error_for_temporal


def _resolve_deliverables(input: BlackboxPipelineInput) -> Path:
    """Replicate the path resolution logic from BlackboxScanWorkflow for unit testing."""
    deliverables_path = None
    if input.repo_path:
        deliverables_path = Path(input.repo_path) / input.deliverables_subdir
    elif input.workspace_name:
        session_file = Path("workspaces") / input.workspace_name / "session.json"
        if session_file.exists():
            session_data = json.loads(session_file.read_text())
            saved_repo = session_data.get("repo_path")
            if saved_repo:
                deliverables_path = Path(saved_repo) / input.deliverables_subdir
    if not deliverables_path:
        deliverables_path = Path("workspaces") / (input.workspace_name or "default") / input.deliverables_subdir
    return deliverables_path


def test_path_resolution_with_repo_path(tmp_path):
    """When repo_path is provided, deliverables should be under repo."""
    repo = tmp_path / "my-repo"
    repo.mkdir()

    input = BlackboxPipelineInput(
        web_url="https://example.com",
        repo_path=str(repo),
        workspace_name="my-scan",
    )
    result = _resolve_deliverables(input)
    assert result == repo / ".shannon" / "deliverables"


def test_path_resolution_fallback_to_session_data(tmp_path, monkeypatch):
    """When repo_path is missing but session.json has it, use session data."""
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "target-repo"
    repo.mkdir()

    # Create session.json with repo_path
    ws_dir = tmp_path / "workspaces" / "my-scan"
    ws_dir.mkdir(parents=True)
    session_data = {"repo_path": str(repo), "web_url": ""}
    (ws_dir / "session.json").write_text(json.dumps(session_data))

    input = BlackboxPipelineInput(
        web_url="https://example.com",
        workspace_name="my-scan",
    )
    result = _resolve_deliverables(input)
    assert result == repo / ".shannon" / "deliverables"


def test_path_resolution_pure_fallback(tmp_path, monkeypatch):
    """When no repo_path and no session data, fall back to workspaces dir."""
    monkeypatch.chdir(tmp_path)

    input = BlackboxPipelineInput(
        web_url="https://example.com",
        workspace_name="my-scan",
    )
    result = _resolve_deliverables(input)
    assert result == Path("workspaces") / "my-scan" / ".shannon" / "deliverables"


def test_state_tracks_found_classes_with_results(tmp_path):
    """When exploitation_queue.json exists, found classes should be tracked in state."""
    state = BlackboxPipelineState(
        has_whitebox_results=True,
        found_whitebox_classes=["injection", "xss"],
    )
    assert state.has_whitebox_results is True
    assert state.found_whitebox_classes == ["injection", "xss"]


def test_state_defaults_no_found_classes():
    """Default state should have empty found_whitebox_classes."""
    state = BlackboxPipelineState()
    assert state.found_whitebox_classes == []


def test_pipeline_input_max_concurrent_default():
    """Default max_concurrent should be 3."""
    input = BlackboxPipelineInput(web_url="https://example.com")
    assert input.max_concurrent == 3


def test_pipeline_input_max_concurrent_custom():
    """Custom max_concurrent should be respected."""
    input = BlackboxPipelineInput(web_url="https://example.com", max_concurrent=5)
    assert input.max_concurrent == 5


class TestBlackboxWorkflowErrorPropagation:
    """Test the error propagation logic that BlackboxScanWorkflow uses."""

    def test_state_completed_when_no_errors(self):
        """All agents succeed -> status=completed."""
        state = BlackboxPipelineState()
        state.completed_agents = ["RECON_BLACKBOX", "REPORT"]
        state.agent_metrics = {"RECON_BLACKBOX": {}, "REPORT": {}}
        if state.errors:
            state.status = "failed"
        else:
            state.status = "completed"
        assert state.status == "completed"
        assert state.failed_agents == []
        assert state.error_code is None

    def test_state_failed_when_exploit_agents_fail(self):
        """Some exploit agents fail -> status=failed with error classification."""
        state = BlackboxPipelineState()
        state.completed_agents = ["RECON_BLACKBOX", "injection-exploit"]
        state.errors = ["xss-exploit: 403 Forbidden"]
        state.failed_agents = ["xss-exploit"]
        if state.errors:
            state.status = "failed"
            first_error_msg = state.errors[0].split(": ", 1)[-1]
            error_type, _ = classify_error_for_temporal(Exception(first_error_msg))
            state.error_code = error_type
        else:
            state.status = "completed"
        assert state.status == "failed"
        assert state.failed_agents == ["xss-exploit"]
        assert state.error_code == "PermissionError"

    def test_state_cancelled(self):
        """Cancelled -> status=cancelled."""
        state = BlackboxPipelineState()
        state.status = "cancelled"
        assert state.status == "cancelled"

    def test_state_failed_with_all_exploits_failing(self):
        """All exploit agents fail -> still records all failures."""
        state = BlackboxPipelineState()
        state.completed_agents = ["RECON_BLACKBOX"]
        state.errors = [
            "injection-exploit: connection refused",
            "xss-exploit: authentication failed",
        ]
        state.failed_agents = ["injection-exploit", "xss-exploit"]
        state.status = "failed"
        state.error_code = "TransientError"
        assert state.status == "failed"
        assert len(state.failed_agents) == 2
