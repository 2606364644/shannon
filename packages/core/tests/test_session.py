import json
import pytest
from pathlib import Path
from shannon_core.session import SessionManager

def test_create_workspace(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    assert ws.exists()
    assert (ws / "session.json").exists()

def test_list_workspaces(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    mgr.create_workspace("https://a.com", "/repo1")
    mgr.create_workspace("https://b.com", "/repo2")
    workspaces = mgr.list_workspaces()
    assert len(workspaces) == 2

def test_get_workspace(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    found = mgr.get_workspace(ws.name)
    assert found is not None
    assert found.name == ws.name

def test_get_workspace_not_found(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    assert mgr.get_workspace("nonexistent") is None

def test_session_json_contains_url(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://test.com", "/repo")
    data = json.loads((ws / "session.json").read_text())
    assert data["web_url"] == "https://test.com"
