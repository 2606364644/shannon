# packages/web/tests/test_members_routes.py
import pytest
from starlette.testclient import TestClient
from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def _app(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    # tmp_workspaces 把 SUPERNOVA_WORKER_ROOT 设成 tmp_path/"workspaces", 而
    # resolve_workspaces_dir() 会再追加 /"workspaces" -> 嵌套一层不存在目录,
    # 致 AuthStore.init_schema() 建不了 auth.db。rebase 到父目录使解析结果
    # 恰好等于 tmp_workspaces (同 conftest.app_with_ws 模式)。
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    app = create_app()
    st = app.state.auth_store
    st.create_user("alice", hash_password("p"))
    st.create_user("bob", hash_password("p"))
    (app.state.config.workspaces_dir / "ws1").mkdir()
    st.add_workspace_member("ws1", st.get_user_by_username("alice").id, "manager")
    return app


def _login(app, username):
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": username, "password": "p"}, headers={"X-CSRF-Token": tok})
    return c


def test_list_users(_app):
    c = _login(_app, "alice")
    names = sorted(u["username"] for u in c.get("/api/users").json()["users"])
    assert names == ["alice", "bob"]


def test_list_members(_app):
    c = _login(_app, "alice")
    members = c.get("/api/workspaces/ws1/members").json()["members"]
    assert any(m["username"] == "alice" and m["role"] == "manager" for m in members)


def test_manager_adds_member(_app):
    c = _login(_app, "alice")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.post("/api/workspaces/ws1/members", json={"username": "bob", "role": "member"},
               headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    members = c.get("/api/workspaces/ws1/members").json()["members"]
    assert any(m["username"] == "bob" for m in members)


def test_member_cannot_add(_app):
    # bob 先被加为 member
    _app.state.auth_store.add_workspace_member("ws1", _app.state.auth_store.get_user_by_username("bob").id, "member")
    c = _login(_app, "bob")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.post("/api/workspaces/ws1/members", json={"username": "alice"}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 403


def test_cannot_remove_last_manager(_app):
    c = _login(_app, "alice")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.delete("/api/workspaces/ws1/members/alice", headers={"X-CSRF-Token": tok})
    assert r.status_code == 409
