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


def test_delete_workspace_removes_child_refs_from_parent(tmp_path):
    """Deleting a blackbox workspace should remove its name from the parent's child_workspaces."""
    mgr = SessionManager(tmp_path / "workspaces")
    parent = mgr.create_workspace("https://example.com", "/repo", name="wb-parent", scan_type="whitebox")
    child = mgr.create_workspace("https://example.com", "/repo", name="bb-child", scan_type="blackbox")
    mgr.add_child_workspace(parent, "bb-child")
    mgr.set_parent_workspace(child, "wb-parent")

    # Verify link exists
    assert "bb-child" in mgr.get_links(parent)["child_workspaces"]

    mgr.delete_workspace("bb-child")

    # Parent should no longer list the deleted child
    assert "bb-child" not in mgr.get_links(parent)["child_workspaces"]
    # Child directory should be gone
    assert not child.exists()


def test_delete_workspace_clears_parent_ref_from_children(tmp_path):
    """Deleting a whitebox workspace should clear parent_workspace in all child workspaces."""
    mgr = SessionManager(tmp_path / "workspaces")
    parent = mgr.create_workspace("https://example.com", "/repo", name="wb-parent-2", scan_type="whitebox")
    child1 = mgr.create_workspace("https://example.com", "/repo", name="bb-child-1a", scan_type="blackbox")
    child2 = mgr.create_workspace("https://example.com", "/repo", name="bb-child-2a", scan_type="blackbox")
    mgr.add_child_workspace(parent, "bb-child-1a")
    mgr.add_child_workspace(parent, "bb-child-2a")
    mgr.set_parent_workspace(child1, "wb-parent-2")
    mgr.set_parent_workspace(child2, "wb-parent-2")

    mgr.delete_workspace("wb-parent-2")

    # Children should have their parent ref cleared
    assert mgr.get_links(child1)["parent_workspace"] is None
    assert mgr.get_links(child2)["parent_workspace"] is None
    # Parent directory should be gone
    assert not parent.exists()


def test_delete_workspace_handles_already_deleted_linked_ws(tmp_path):
    """Deleting a workspace with links to already-removed workspaces should not error."""
    mgr = SessionManager(tmp_path / "workspaces")
    parent = mgr.create_workspace("https://example.com", "/repo", name="wb-orphan", scan_type="whitebox")
    # Manually add a child reference to a workspace that doesn't exist on disk
    mgr.add_child_workspace(parent, "ghost-child")

    # Should not raise
    result = mgr.delete_workspace("wb-orphan")
    assert result is True


def test_clean_workspace_whitebox(tmp_path):
    """clean_workspace with whitebox should remove artifacts but keep session.json."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="wb-clean", scan_type="whitebox")
    from shannon_core.models.agents import AgentName
    mgr.mark_agent_completed(ws, AgentName.RECON)
    # Create artifacts
    (ws / "deliverables").mkdir()
    (ws / "deliverables" / "injection_exploitation_queue.json").write_text("[]", encoding="utf-8")
    (ws / "agents").mkdir()
    (ws / "agents" / "recon.log").write_text("log data", encoding="utf-8")
    (ws / "prompts").mkdir()
    (ws / "prompts" / "recon.txt").write_text("prompt", encoding="utf-8")
    (ws / "scratchpad").mkdir()
    (ws / "scratchpad" / "temp.txt").write_text("temp", encoding="utf-8")
    (ws / "workflow.log").write_text("workflow log", encoding="utf-8")
    (ws / ".playwright").mkdir()
    (ws / ".playwright-cli").mkdir()

    mgr.clean_workspace(ws, scan_type="whitebox")

    # session.json must survive
    assert (ws / "session.json").exists()
    # completed_agents should be reset
    data = json.loads((ws / "session.json").read_text(encoding="utf-8"))
    assert data["completed_agents"] == []
    assert data["deliverables_summary"] is None
    # Artifact dirs/files should be gone
    assert not (ws / "deliverables").exists()
    assert not (ws / "agents").exists()
    assert not (ws / "prompts").exists()
    assert not (ws / "scratchpad").exists()
    assert not (ws / "workflow.log").exists()
    assert not (ws / ".playwright").exists()
    assert not (ws / ".playwright-cli").exists()


def test_clean_workspace_blackbox(tmp_path):
    """clean_workspace with blackbox should remove blackbox-specific artifacts."""
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace("https://example.com", "/repo", name="bb-clean", scan_type="blackbox")
    # Create blackbox-style artifacts
    (ws / "deliverables").mkdir()
    (ws / "deliverables" / "injection_exploitation_evidence.md").write_text("evidence", encoding="utf-8")
    (ws / "deliverables" / "xss_findings.md").write_text("findings", encoding="utf-8")
    (ws / "deliverables" / "comprehensive_security_assessment_report.md").write_text("report", encoding="utf-8")
    (ws / "deliverables" / "injection_exploitation_queue.json").write_text("[]", encoding="utf-8")
    (ws / "agents").mkdir()
    (ws / "agents" / "injection-exploit_001.log").write_text("exploit log", encoding="utf-8")
    (ws / "agents" / "ssrf-validate-authentication_002.log").write_text("auth log", encoding="utf-8")
    (ws / "agents" / "recon.log").write_text("recon log", encoding="utf-8")
    (ws / "workflow.log").write_text("workflow log", encoding="utf-8")
    (ws / ".playwright").mkdir()
    (ws / ".playwright-cli").mkdir()

    mgr.clean_workspace(ws, scan_type="blackbox")

    # session.json must survive
    assert (ws / "session.json").exists()
    # Blackbox-specific deliverables removed
    assert not (ws / "deliverables" / "injection_exploitation_evidence.md").exists()
    assert not (ws / "deliverables" / "xss_findings.md").exists()
    assert not (ws / "deliverables" / "comprehensive_security_assessment_report.md").exists()
    # Exploitation queues are NOT removed (they are whitebox deliverables)
    assert (ws / "deliverables" / "injection_exploitation_queue.json").exists()
    # Blackbox agent logs removed
    assert not (ws / "agents" / "injection-exploit_001.log").exists()
    assert not (ws / "agents" / "ssrf-validate-authentication_002.log").exists()
    # Non-blackbox agent logs kept
    assert (ws / "agents" / "recon.log").exists()
    # workflow.log truncated (empty file remains)
    assert (ws / "workflow.log").read_text() == ""
    # Playwright dirs removed
    assert not (ws / ".playwright").exists()
    assert not (ws / ".playwright-cli").exists()
