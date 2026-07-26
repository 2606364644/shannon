import pytest
from starlette.testclient import TestClient
from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def _app(tmp_workspaces, monkeypatch):
    # 与 conftest.app_with_ws / test_workspace_filter._setup 同模式:
    # tmp_workspaces 设 SUPERNOVA_WORKER_ROOT=tmp_path/workspaces, 但
    # resolve_workspaces_dir() 再追加 /workspaces -> 嵌套一层且不存在, auth.db 打不开.
    # 改回 tmp_workspaces.parent 使 workspaces_dir == tmp_workspaces.
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    app = create_app()
    st = app.state.auth_store
    st.create_user("admin", hash_password("p"), role="admin")
    st.create_user("alice", hash_password("p"))
    return app


def _login(app, username):
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": username, "password": "p"}, headers={"X-CSRF-Token": tok})
    return c


def test_admin_creates_workspace(_app):
    c = _login(_app, "admin")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.post("/api/workspaces", json={"name": "ws1"}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 201
    assert (_app.state.config.workspaces_dir / "ws1").is_dir()
    admin = _app.state.auth_store.get_user_by_username("admin")
    assert _app.state.auth_store.get_workspace_member_role("ws1", admin.id) == "manager"


def test_non_admin_cannot_create_workspace(_app):
    c = _login(_app, "alice")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    assert c.post("/api/workspaces", json={"name": "ws2"}, headers={"X-CSRF-Token": tok}).status_code == 403


def test_create_workspace_conflict(_app):
    c = _login(_app, "admin")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/workspaces", json={"name": "ws1"}, headers={"X-CSRF-Token": tok})
    assert c.post("/api/workspaces", json={"name": "ws1"}, headers={"X-CSRF-Token": tok}).status_code == 409


def test_scan_requires_existing_workspace(_app, monkeypatch):
    async def _fake_start(req):
        return req.workspace
    _app.state.scan_manager.start = _fake_start
    c = _login(_app, "alice")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.post("/api/scan", json={"type": "whitebox", "workspace": "nope", "url": "http://x"},
               headers={"X-CSRF-Token": tok})
    assert r.status_code == 422  # ws 不存在


def test_created_workspace_visible_in_list(_app):
    # final-review I1: POST /api/workspaces 建的 ws 必须在 GET /api/workspaces 可见。
    # create_workspace 原本只 mkdir + add_workspace_member, 未写 session.json ->
    # list_workspaces 经 SessionManager.list_workspaces 过滤 (p/session.json).exists()
    # -> 新建 ws 不可见。修复: create_workspace 写最小 session.json(status=completed)。
    c = _login(_app, "admin")
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.post("/api/workspaces", json={"name": "ws1"}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 201
    names = [w["name"] for w in c.get("/api/workspaces").json()]
    assert "ws1" in names


def test_scan_requires_membership(_app, monkeypatch):
    admin_c = _login(_app, "admin")
    atok = admin_c.get("/api/auth/csrf").json()["csrf_token"]
    admin_c.post("/api/workspaces", json={"name": "ws1"}, headers={"X-CSRF-Token": atok})  # admin 建 ws1
    async def _fake_start(req):
        return req.workspace
    _app.state.scan_manager.start = _fake_start
    alice_c = _login(_app, "alice")
    altok = alice_c.get("/api/auth/csrf").json()["csrf_token"]
    r = alice_c.post("/api/scan", json={"type": "whitebox", "workspace": "ws1", "url": "http://x"},
                     headers={"X-CSRF-Token": altok})
    assert r.status_code == 403  # alice 非 ws1 成员
