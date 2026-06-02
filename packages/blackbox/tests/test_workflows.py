import json
from pathlib import Path

from shannon_blackbox.pipeline.shared import BlackboxPipelineInput


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
