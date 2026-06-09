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

def test_create_workspace_includes_scan_type(tmp_path):
    """create_workspace should accept and persist scan_type."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", scan_type="whitebox")
    data = json.loads((ws / "session.json").read_text())
    assert data["scan_type"] == "whitebox"


def test_create_workspace_defaults_scan_type(tmp_path):
    """create_workspace should default scan_type to 'whitebox'."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    data = json.loads((ws / "session.json").read_text())
    assert data["scan_type"] == "whitebox"


def test_create_workspace_blackbox_scan_type(tmp_path):
    """create_workspace with scan_type='blackbox'."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", scan_type="blackbox")
    data = json.loads((ws / "session.json").read_text())
    assert data["scan_type"] == "blackbox"


def test_get_scan_type_explicit(tmp_path):
    """get_scan_type returns explicit scan_type from session.json."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", scan_type="blackbox")
    assert mgr.get_scan_type(ws) == "blackbox"


def test_get_scan_type_inferred_from_name(tmp_path):
    """get_scan_type infers from workspace name containing 'blackbox'."""
    ws = tmp_path / "workspaces" / "myapp-blackbox-123"
    ws.mkdir(parents=True)
    (ws / "session.json").write_text(json.dumps({"web_url": "https://example.com"}))
    mgr = SessionManager(tmp_path / "workspaces")
    assert mgr.get_scan_type(ws) == "blackbox"


def test_get_scan_type_defaults_whitebox(tmp_path):
    """get_scan_type defaults to whitebox when no clue exists."""
    ws = tmp_path / "workspaces" / "myapp-123"
    ws.mkdir(parents=True)
    (ws / "session.json").write_text(json.dumps({"web_url": "https://example.com"}))
    mgr = SessionManager(tmp_path / "workspaces")
    assert mgr.get_scan_type(ws) == "whitebox"


def test_get_status_from_session(tmp_path):
    """get_status reads status field from session.json."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    assert mgr.get_status(ws) == "running"


def test_get_status_legacy_format(tmp_path):
    """get_status handles legacy nested session.status format."""
    ws = tmp_path / "workspaces" / "legacy-ws"
    ws.mkdir(parents=True)
    (ws / "session.json").write_text(json.dumps({
        "session": {"id": "legacy-ws", "status": "completed"},
        "metrics": {},
    }))
    mgr = SessionManager(tmp_path / "workspaces")
    assert mgr.get_status(ws) == "completed"


def test_get_status_unknown_when_empty(tmp_path):
    """get_status returns 'unknown' when no status info exists."""
    ws = tmp_path / "workspaces" / "empty-ws"
    ws.mkdir(parents=True)
    (ws / "session.json").write_text(json.dumps({"web_url": "https://example.com"}))
    mgr = SessionManager(tmp_path / "workspaces")
    assert mgr.get_status(ws) == "unknown"


def test_get_web_url(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    assert mgr.get_web_url(ws) == "https://example.com"


def test_get_web_url_legacy_format(tmp_path):
    ws = tmp_path / "workspaces" / "legacy-ws"
    ws.mkdir(parents=True)
    (ws / "session.json").write_text(json.dumps({"session": {"webUrl": "https://legacy.com"}}))
    mgr = SessionManager(tmp_path / "workspaces")
    assert mgr.get_web_url(ws) == "https://legacy.com"


def test_get_created_at(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    assert mgr.get_created_at(ws) is not None
    assert isinstance(mgr.get_created_at(ws), float)


def test_get_completed_at_before_completion(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    assert mgr.get_completed_at(ws) is None


def test_get_links(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    links = mgr.get_links(ws)
    assert links["parent_workspace"] is None
    assert links["child_workspaces"] == []


def test_set_parent_workspace(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", scan_type="blackbox")
    mgr.set_parent_workspace(ws, "wb-parent")
    links = mgr.get_links(ws)
    assert links["parent_workspace"] == "wb-parent"


def test_add_child_workspace(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    mgr.add_child_workspace(ws, "bb-child-1")
    links = mgr.get_links(ws)
    assert "bb-child-1" in links["child_workspaces"]


def test_add_child_workspace_deduplicates(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    mgr.add_child_workspace(ws, "bb-child-1")
    mgr.add_child_workspace(ws, "bb-child-1")
    links = mgr.get_links(ws)
    assert links["child_workspaces"].count("bb-child-1") == 1


def test_mark_completed(tmp_path):
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo")
    assert mgr.get_status(ws) == "running"
    assert mgr.get_completed_at(ws) is None
    mgr.mark_completed(ws)
    assert mgr.get_status(ws) == "completed"
    assert mgr.get_completed_at(ws) is not None
    assert isinstance(mgr.get_completed_at(ws), float)


def test_delete_workspace_removes_directory(tmp_path):
    """delete_workspace should remove the entire workspace directory."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="to-delete")
    assert ws.exists()
    result = mgr.delete_workspace("to-delete")
    assert result is True
    assert not ws.exists()


def test_delete_workspace_returns_false_when_not_found(tmp_path):
    """delete_workspace should return False for nonexistent workspace."""
    mgr = SessionManager(tmp_path / "workspaces")
    result = mgr.delete_workspace("nonexistent")
    assert result is False
