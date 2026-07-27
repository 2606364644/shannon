import pytest
from starlette.testclient import TestClient

from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def admin_client(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_core.utils.paths import resolve_workspaces_dir
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    assert resolve_workspaces_dir() == tmp_workspaces
    app = create_app()
    app.state.auth_store.create_user("admin", hash_password("admin-pw"), role="admin")
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": "admin", "password": "admin-pw"},
           headers={"X-CSRF-Token": tok})
    return c, app


def _csrf(c):
    return c.cookies.get("sn-csrf") or c.get("/api/auth/csrf").json()["csrf_token"]


def test_patch_member_role_success(admin_client):
    c, app = admin_client
    st = app.state.auth_store
    u = st.create_user("alice", "h")
    st.add_workspace_member("ws-a", u.id, "member")
    r = c.patch("/api/workspaces/ws-a/members/alice", json={"role": "manager"},
                headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 200
    assert st.get_workspace_member_role("ws-a", u.id) == "manager"


def test_patch_member_role_last_manager_409(admin_client):
    """降最后 manager -> 409（复用 remove_member 的护栏逻辑）。"""
    c, app = admin_client
    st = app.state.auth_store
    u = st.create_user("alice", "h")
    st.add_workspace_member("ws-a", u.id, "manager")  # 唯一 manager
    r = c.patch("/api/workspaces/ws-a/members/alice", json={"role": "member"},
                headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 409


def test_patch_member_role_bad_role_422(admin_client):
    c, app = admin_client
    st = app.state.auth_store
    u = st.create_user("alice", "h")
    st.add_workspace_member("ws-a", u.id, "member")
    r = c.patch("/api/workspaces/ws-a/members/alice", json={"role": "owner"},
                headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 422


def test_patch_member_role_user_not_found_404(admin_client):
    c, app = admin_client
    r = c.patch("/api/workspaces/ws-a/members/nobody", json={"role": "manager"},
                headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 404
