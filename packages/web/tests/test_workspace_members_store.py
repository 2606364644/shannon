from supernova_web.auth.store import AuthStore


def _store(tmp_path):
    s = AuthStore(str(tmp_path / "auth.db")); s.init_schema()
    s.create_user("admin", "h", role="admin")
    s.create_user("alice", "h")
    s.create_user("bob", "h")
    return s


def test_add_and_list_members(tmp_path):
    s = _store(tmp_path)
    s.add_workspace_member("ws1", 2, "manager")
    s.add_workspace_member("ws1", 3, "member")
    members = s.list_workspace_members("ws1")
    assert (2, "alice", "manager") in members
    assert (3, "bob", "member") in members
    assert s.list_workspace_members("ws2") == []


def test_get_role_and_list_user_workspaces(tmp_path):
    s = _store(tmp_path)
    s.add_workspace_member("ws1", 2, "manager")
    s.add_workspace_member("ws2", 2, "member")
    assert s.get_workspace_member_role("ws1", 2) == "manager"
    assert s.get_workspace_member_role("ws1", 3) is None
    assert set(s.list_user_workspaces(2)) == {"ws1", "ws2"}


def test_remove_and_delete(tmp_path):
    s = _store(tmp_path)
    s.add_workspace_member("ws1", 2, "manager")
    s.add_workspace_member("ws1", 3, "member")
    s.remove_workspace_member("ws1", 3)
    assert s.get_workspace_member_role("ws1", 3) is None
    assert s.delete_workspace_members("ws1") == 1
    assert s.list_workspace_members("ws1") == []


def test_list_all_users(tmp_path):
    s = _store(tmp_path)
    names = [u.username for u in s.list_all_users()]
    assert names == ["admin", "alice", "bob"]
