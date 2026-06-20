import json
import pytest
from pathlib import Path

from shannon_core.utils.paths import resolve_workspaces_dir, resolve_deliverables_path, has_valid_whitebox_results, get_default_deliverables_subdir, deliverables_dir_for_workspace


class TestResolveWorkspacesDir:
    def test_with_repo_path_ignored(self, tmp_path, monkeypatch):
        """repo_path 不再决定 workspace 根(改为 project_root/workspaces)。"""
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        monkeypatch.chdir(project_root)
        monkeypatch.delenv("SHANNON_WORKER_ROOT", raising=False)
        result = resolve_workspaces_dir("/data/repos/myrepo")
        assert result == project_root / "workspaces"

    def test_with_repo_path_nested_ignored(self, tmp_path, monkeypatch):
        """repo_path 不再决定 workspace 根(改为 project_root/workspaces)。"""
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        monkeypatch.chdir(project_root)
        monkeypatch.delenv("SHANNON_WORKER_ROOT", raising=False)
        result = resolve_workspaces_dir("/a/b/c")
        assert result == project_root / "workspaces"

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

    def test_with_worker_root_env(self, tmp_path, monkeypatch):
        """When SHANNON_WORKER_ROOT is set, returns worker_root / workspaces."""
        worker_root = tmp_path / "shannon-worker"
        worker_root.mkdir()
        monkeypatch.setenv("SHANNON_WORKER_ROOT", str(worker_root))
        result = resolve_workspaces_dir()
        assert result == worker_root / "workspaces"

    def test_worker_root_env_used_even_when_repo_path_given(self, monkeypatch):
        """SHANNON_WORKER_ROOT 优先级高于 repo_path(repo_path 已不再参与定位 workspace 根)。"""
        monkeypatch.setenv("SHANNON_WORKER_ROOT", "/custom/worker/root")
        result = resolve_workspaces_dir("/data/repos/myrepo")
        assert result == Path("/custom/worker/root/workspaces")

    def test_worker_root_fallback_without_repo_path(self, tmp_path, monkeypatch):
        """When no repo_path and no SHANNON_WORKER_ROOT, uses project_root."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".git").mkdir()
        monkeypatch.chdir(project_root)
        monkeypatch.delenv("SHANNON_WORKER_ROOT", raising=False)
        result = resolve_workspaces_dir()
        assert result == project_root / "workspaces"


class TestResolveDeliverablesPath:
    def test_workspace_name_takes_priority(self, tmp_path):
        """workspace_name 优先 → workspaces/<name>/deliverables，忽略 repo_path。"""
        result = resolve_deliverables_path(
            repo_path="/data/repos/myrepo",
            deliverables_subdir="deliverables",
            workspace_name="scan-1",
            workspaces_root=tmp_path / "workspaces",
        )
        assert result == tmp_path / "workspaces" / "scan-1" / "deliverables"

    def test_workspace_name_without_workspaces_root(self, tmp_path, monkeypatch):
        """workspace_name 但未传 workspaces_root → 用 resolve_workspaces_dir()。"""
        monkeypatch.setenv("SHANNON_WORKER_ROOT", str(tmp_path / "worker"))
        result = resolve_deliverables_path(
            repo_path=None,
            deliverables_subdir="deliverables",
            workspace_name="scan-1",
        )
        assert result == tmp_path / "worker" / "workspaces" / "scan-1" / "deliverables"

    def test_repo_path_fallback_when_no_workspace(self):
        """无 workspace_name 时过渡回退 repo_path/<subdir>。"""
        result = resolve_deliverables_path(
            repo_path="/data/repos/myrepo",
            deliverables_subdir="deliverables",
        )
        assert result == Path("/data/repos/myrepo/deliverables")

    def test_raises_when_no_repo_or_workspace(self):
        with pytest.raises(ValueError, match="必须提供"):
            resolve_deliverables_path(
                repo_path=None,
                deliverables_subdir="deliverables",
            )


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


class TestDeliverablesDirForWorkspace:
    """deliverables_dir_for_workspace 直接返回 workspace 下的 deliverables 目录。"""

    def test_returns_workspace_deliverables(self, tmp_path):
        from shannon_core.session import SessionManager

        repo = tmp_path / "repo"
        repo.mkdir()
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://x.com", str(repo), name="wb-1")

        assert deliverables_dir_for_workspace(ws) == ws / "deliverables"

    def test_works_without_session_json(self, tmp_path):
        ws = tmp_path / "workspaces" / "orphan"
        ws.mkdir(parents=True)
        assert deliverables_dir_for_workspace(ws) == ws / "deliverables"
