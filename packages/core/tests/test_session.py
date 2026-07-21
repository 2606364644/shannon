import json
import re
import pytest
from datetime import datetime
from pathlib import Path
from supernova_core.session import SessionManager

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

def test_create_workspace_names_after_repo_basename_when_no_url(tmp_path):
    """web_url 为空时用 repo basename 命名，不以空 hostname 开头。"""
    mgr = SessionManager(tmp_path / "workspaces")
    repo = tmp_path / "myapp"
    repo.mkdir()
    ws = mgr.create_workspace(web_url="", repo_path=str(repo), name=None)
    assert ws.name.startswith("myapp_")
    assert re.search(r"\d{8}-\d{6}$", ws.name), ws.name


def test_create_workspace_names_after_hostname_when_url_given(tmp_path):
    """web_url 非空时仍用 hostname（不回退到 repo basename）。"""
    mgr = SessionManager(tmp_path / "workspaces")
    repo = tmp_path / "myapp"
    repo.mkdir()
    ws = mgr.create_workspace(web_url="https://git.example.com/x/y", repo_path=str(repo), name=None)
    assert ws.name.startswith("git-example-com_")
    assert re.search(r"\d{8}-\d{6}$", ws.name), ws.name


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


def test_workspace_name_human_readable_format(tmp_path):
    """新 workspace 名为 <hostname>_YYYYMMDD-HHMMSS（本地时区紧凑秒级，无冒号）。"""
    mgr = SessionManager(tmp_path / "workspaces")
    before = datetime.now()
    ws = mgr.create_workspace(web_url="", repo_path="/repo/NodeGoat", name=None)
    # 格式：hostname_YYYYMMDD-HHMMSS
    assert re.match(r"^NodeGoat_\d{8}-\d{6}$", ws.name), ws.name
    # 日期部分 = 今天（本地时区），证明是真实当前时间而非占位
    parsed = datetime.strptime(ws.name.split("_", 1)[1], "%Y%m%d-%H%M%S")
    assert parsed.strftime("%Y%m%d") == before.strftime("%Y%m%d")


def test_legacy_timestamp_dirs_still_listable(tmp_path):
    """老格式目录（shannon-<毫秒>）仍能被 list_workspaces / get_session_data 处理。"""
    mgr = SessionManager(tmp_path / "workspaces")
    legacy = tmp_path / "workspaces" / "NodeGoat_shannon-1782041072350"
    legacy.mkdir()
    (legacy / "session.json").write_text(json.dumps({
        "web_url": "",
        "repo_path": "/repo",
        "created_at": 1782041072.350,
        "scan_type": "whitebox",
        "status": "completed",
    }))
    workspaces = mgr.list_workspaces()
    assert legacy in workspaces
    data = mgr.get_session_data(legacy)
    assert data["scan_type"] == "whitebox"


def test_workspace_name_collision_appends_suffix(tmp_path, monkeypatch):
    """同秒同名二次创建追加 -2，不覆盖既有 session.json，两目录独立。

    用 monkeypatch 冻结 session 模块的 datetime，保证两次调用生成相同 base
    （deterministic，不依赖真实时钟同秒——避免 flaky）。
    """
    import supernova_core.session as session_mod
    fixed = datetime(2026, 6, 19, 14, 30, 0)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(session_mod, "datetime", _FixedDateTime)
    mgr = SessionManager(tmp_path / "workspaces")
    ws1 = mgr.create_workspace(web_url="", repo_path="/repo/NodeGoat", name=None)
    ws2 = mgr.create_workspace(web_url="", repo_path="/repo/NodeGoat", name=None)
    assert ws1.name == "NodeGoat_20260619-143000", ws1.name
    assert ws2.name == "NodeGoat_20260619-143000-2", ws2.name
    assert ws1 != ws2
    assert (ws1 / "session.json").exists()
    assert (ws2 / "session.json").exists()


def test_explicit_name_keeps_idempotent_return(tmp_path):
    """显式传 name（resume 场景）+ session.json 已存在 → 幂等 return 同一目录，不追加序号、不覆盖。"""
    mgr = SessionManager(tmp_path / "workspaces")
    ws1 = mgr.create_workspace(web_url="", repo_path="/repo", name="myapp_run1")
    assert ws1.name == "myapp_run1"
    # 同名 resume → 应返回同一目录，不得变成 myapp_run1-2
    ws2 = mgr.create_workspace(web_url="", repo_path="/repo", name="myapp_run1")
    assert ws2 == ws1
    assert ws2.name == "myapp_run1"
