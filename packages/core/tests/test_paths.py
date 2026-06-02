import json
import pytest
from pathlib import Path

from shannon_core.utils.paths import resolve_workspaces_dir, resolve_deliverables_path, has_valid_whitebox_results


class TestResolveWorkspacesDir:
    def test_with_repo_path(self):
        result = resolve_workspaces_dir("/data/repos/myrepo")
        assert result == Path("/data/repos/workspaces")

    def test_with_repo_path_nested(self):
        result = resolve_workspaces_dir("/a/b/c")
        assert result == Path("/a/b/workspaces")

    def test_without_repo_path(self):
        result = resolve_workspaces_dir()
        assert result == Path("workspaces")


class TestResolveDeliverablesPath:
    def test_with_repo_path(self):
        result = resolve_deliverables_path(
            repo_path="/data/repos/myrepo",
            deliverables_subdir=".shannon/deliverables",
        )
        assert result == Path("/data/repos/myrepo/.shannon/deliverables")

    def test_with_workspace_name_reads_session(self, tmp_path):
        ws_dir = tmp_path / "workspaces" / "scan-1"
        ws_dir.mkdir(parents=True)
        session_data = {"repo_path": "/data/repos/myrepo", "web_url": "https://example.com"}
        (ws_dir / "session.json").write_text(json.dumps(session_data))

        result = resolve_deliverables_path(
            repo_path=None,
            deliverables_subdir=".shannon/deliverables",
            workspace_name="scan-1",
            workspaces_root=tmp_path / "workspaces",
        )
        assert result == Path("/data/repos/myrepo/.shannon/deliverables")

    def test_with_workspace_name_fallback_when_no_session(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        result = resolve_deliverables_path(
            repo_path=None,
            deliverables_subdir=".shannon/deliverables",
            workspace_name="scan-1",
            workspaces_root=ws_dir,
        )
        assert result == ws_dir / "scan-1" / ".shannon/deliverables"

    def test_with_workspace_name_fallback_when_no_repo_in_session(self, tmp_path):
        ws_dir = tmp_path / "workspaces" / "scan-1"
        ws_dir.mkdir(parents=True)
        session_data = {"web_url": "https://example.com"}
        (ws_dir / "session.json").write_text(json.dumps(session_data))

        result = resolve_deliverables_path(
            repo_path=None,
            deliverables_subdir=".shannon/deliverables",
            workspace_name="scan-1",
            workspaces_root=tmp_path / "workspaces",
        )
        assert result == tmp_path / "workspaces" / "scan-1" / ".shannon/deliverables"

    def test_raises_when_no_repo_or_workspace(self):
        with pytest.raises(ValueError, match="必须提供"):
            resolve_deliverables_path(
                repo_path=None,
                deliverables_subdir=".shannon/deliverables",
            )

    def test_repo_path_takes_priority_over_workspace(self, tmp_path):
        ws_dir = tmp_path / "workspaces" / "scan-1"
        ws_dir.mkdir(parents=True)
        session_data = {"repo_path": "/other/repo"}
        (ws_dir / "session.json").write_text(json.dumps(session_data))

        result = resolve_deliverables_path(
            repo_path="/data/repos/myrepo",
            deliverables_subdir=".shannon/deliverables",
            workspace_name="scan-1",
            workspaces_root=tmp_path / "workspaces",
        )
        assert result == Path("/data/repos/myrepo/.shannon/deliverables")


class TestHasValidWhiteboxResults:
    def test_file_not_found(self, tmp_path):
        assert has_valid_whitebox_results(tmp_path / "nonexistent.json") is False

    def test_valid_vulnerabilities(self, tmp_path):
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({"vulnerabilities": [{"ID": "V-001"}]}))
        assert has_valid_whitebox_results(queue_file) is True

    def test_empty_vulnerabilities(self, tmp_path):
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({"vulnerabilities": []}))
        assert has_valid_whitebox_results(queue_file) is False

    def test_missing_vulnerabilities_key(self, tmp_path):
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({"data": "something"}))
        assert has_valid_whitebox_results(queue_file) is False

    def test_invalid_json(self, tmp_path):
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text("not json")
        assert has_valid_whitebox_results(queue_file) is False

    def test_vulnerabilities_not_a_list(self, tmp_path):
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({"vulnerabilities": "not a list"}))
        assert has_valid_whitebox_results(queue_file) is False
