import json
import pytest
from pathlib import Path

from shannon_core.utils.paths import resolve_workspaces_dir, resolve_deliverables_path, has_valid_whitebox_results, get_default_deliverables_subdir


class TestResolveWorkspacesDir:
    def test_with_repo_path(self):
        result = resolve_workspaces_dir("/data/repos/myrepo")
        assert result == Path("/data/repos/workspaces")

    def test_with_repo_path_nested(self):
        result = resolve_workspaces_dir("/a/b/c")
        assert result == Path("/a/b/workspaces")

    def test_without_repo_path(self, tmp_path, monkeypatch):
        """When no repo_path and in a git repo, resolves to project_root/workspaces."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        monkeypatch.chdir(project_root)
        result = resolve_workspaces_dir()
        assert result == project_root / "workspaces"

    def test_without_repo_path_uses_project_root(self, tmp_path, monkeypatch):
        """When no repo_path, should resolve to project_root/workspaces, not CWD."""
        # Create a fake project root with .git and a subdirectory
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        subdir = project_root / "subdir"
        subdir.mkdir()

        # CWD is inside the project, should find project_root by walking up
        monkeypatch.chdir(subdir)

        result = resolve_workspaces_dir()
        assert result == project_root / "workspaces"


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
        queue_file.write_text(json.dumps({
            "vulnerabilities": [{
                "title": "V-001",
                "description": "Test vulnerability",
                "severity": "medium",
                "location": "test.py:1",
            }]
        }))
        assert has_valid_whitebox_results(queue_file) is True

    def test_valid_with_required_fields(self, tmp_path):
        """Vulnerability entries with all required fields should pass validation."""
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({
            "vulnerabilities": [{
                "title": "SQL Injection",
                "description": "User input concatenated into SQL query",
                "severity": "high",
                "location": "src/api/users.py:42",
            }]
        }))
        assert has_valid_whitebox_results(queue_file) is True

    def test_rejects_missing_required_fields(self, tmp_path):
        """Vulnerability entries missing required fields should be rejected."""
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({
            "vulnerabilities": [{
                "title": "SQL Injection",
                # Missing: description, severity, location
            }]
        }))
        assert has_valid_whitebox_results(queue_file) is False

    def test_rejects_non_dict_entries(self, tmp_path):
        """Non-dict entries in vulnerabilities should be rejected."""
        queue_file = tmp_path / "injection_exploitation_queue.json"
        queue_file.write_text(json.dumps({
            "vulnerabilities": ["not a dict", 42]
        }))
        assert has_valid_whitebox_results(queue_file) is False

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


class TestGetDefaultDeliverablesSubdir:
    def test_returns_constant_when_no_env(self, monkeypatch):
        """When SHANNON_DELIVERABLES_SUBDIR is not set, returns the default constant."""
        from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR
        monkeypatch.delenv("SHANNON_DELIVERABLES_SUBDIR", raising=False)
        assert get_default_deliverables_subdir() == DEFAULT_DELIVERABLES_SUBDIR

    def test_returns_env_value_when_set(self, monkeypatch):
        """When SHANNON_DELIVERABLES_SUBDIR is set, returns its value."""
        monkeypatch.setenv("SHANNON_DELIVERABLES_SUBDIR", "custom/output")
        assert get_default_deliverables_subdir() == "custom/output"

    def test_returns_empty_string_when_env_empty(self, monkeypatch):
        """When SHANNON_DELIVERABLES_SUBDIR is set to empty string, returns empty string."""
        monkeypatch.setenv("SHANNON_DELIVERABLES_SUBDIR", "")
        assert get_default_deliverables_subdir() == ""
