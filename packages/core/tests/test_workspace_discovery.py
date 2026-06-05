import json
from pathlib import Path

import pytest

from shannon_core.services.workspace_discovery import (
    DiscoveryResult,
    ValidationResult,
    WorkspaceDiscovery,
)
from shannon_core.session import SessionManager


def _setup_workspace(tmp_path, name, web_url, vuln_classes, scan_type="whitebox"):
    """Helper to create a workspace with deliverables."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace(web_url, "/repo", name=name, scan_type=scan_type)
    mgr.mark_completed(ws)
    deliverables = ws / "deliverables"
    deliverables.mkdir(parents=True)
    for vc in vuln_classes:
        (deliverables / f"{vc}_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{
                "title": "V", "description": "d", "severity": "high", "location": "a.py:1"
            }]}),
            encoding="utf-8",
        )
    return ws


class TestWorkspaceDiscoveryFindForBlackbox:
    def test_find_by_latest(self, tmp_path):
        _setup_workspace(tmp_path, "ws-1", "https://myapp.com", ["injection"])
        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        result = discovery.find_for_blackbox("https://myapp.com", latest=True)
        assert isinstance(result, DiscoveryResult)
        assert result.workspace_path is not None
        assert result.workspace_path.name == "ws-1"

    def test_find_by_name(self, tmp_path):
        _setup_workspace(tmp_path, "ws-named", "https://myapp.com", ["xss"])
        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        result = discovery.find_for_blackbox("https://myapp.com", workspace_name="ws-named")
        assert result.workspace_path is not None
        assert result.workspace_path.name == "ws-named"

    def test_find_no_match(self, tmp_path):
        _setup_workspace(tmp_path, "ws-1", "https://other.com", ["injection"])
        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        result = discovery.find_for_blackbox("https://myapp.com", latest=True)
        assert result.workspace_path is None


class TestWorkspaceDiscoveryList:
    def test_list_workspaces_for_url(self, tmp_path):
        _setup_workspace(tmp_path, "ws-1", "https://myapp.com", ["injection"])
        _setup_workspace(tmp_path, "ws-2", "https://other.com", ["xss"])
        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        results = discovery.list_whitebox_workspaces("https://myapp.com")
        assert len(results) == 1
        assert results[0].name == "ws-1"

    def test_list_all_workspaces(self, tmp_path):
        _setup_workspace(tmp_path, "ws-1", "https://myapp.com", ["injection"])
        _setup_workspace(tmp_path, "ws-2", "https://other.com", ["xss"])
        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        results = discovery.list_whitebox_workspaces()
        assert len(results) == 2


class TestWorkspaceDiscoveryValidate:
    def test_validate_valid_workspace(self, tmp_path):
        ws = _setup_workspace(tmp_path, "ws-valid", "https://myapp.com", ["injection"])
        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        result = discovery.validate_for_consumption(ws)
        assert isinstance(result, ValidationResult)
        assert result.valid is True

    def test_validate_no_deliverables(self, tmp_path):
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", "/repo", name="ws-empty")
        mgr.mark_completed(ws)
        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        result = discovery.validate_for_consumption(ws)
        assert result.valid is False
        assert len(result.errors) > 0
