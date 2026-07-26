import pytest
from fastapi import HTTPException, Request
from supernova_web.auth.dependencies import workspace_member, workspace_manager
from supernova_web.auth.models import User


class _FakeStore:
    def __init__(self, role_map): self._m = role_map  # {(ws,uid): role}
    def get_workspace_member_role(self, ws, uid): return self._m.get((ws, uid))


def _req(user, store):
    class _App: pass
    app = _App()
    app.state = type("S", (), {"auth_store": store})()
    r = Request({"type": "http", "app": app})
    r.state.user = user
    return r


def test_admin_passes_workspace_member():
    admin = User(id=1, username="admin", role="admin")
    assert workspace_member(_req(admin, _FakeStore({})), "ws1", admin).role == "admin"


def test_member_passes():
    alice = User(id=2, username="alice", role="user")
    store = _FakeStore({("ws1", 2): "member"})
    assert workspace_member(_req(alice, store), "ws1", alice).id == 2


def test_non_member_forbidden():
    alice = User(id=2, username="alice", role="user")
    with pytest.raises(HTTPException) as e:
        workspace_member(_req(alice, _FakeStore({})), "ws1", alice)
    assert e.value.status_code == 403


def test_member_not_manager():
    alice = User(id=2, username="alice", role="user")
    store = _FakeStore({("ws1", 2): "member"})
    with pytest.raises(HTTPException) as e:
        workspace_manager(_req(alice, store), "ws1", alice)
    assert e.value.status_code == 403


def test_manager_passes_workspace_manager():
    alice = User(id=2, username="alice", role="user")
    store = _FakeStore({("ws1", 2): "manager"})
    assert workspace_manager(_req(alice, store), "ws1", alice).id == 2
