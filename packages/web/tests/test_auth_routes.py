import pytest
from starlette.testclient import TestClient

from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def client(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")  # 测试走 HTTP
    # 对齐 conftest.app_with_ws 的 remount：tmp_workspaces 把 SUPERNOVA_WORKER_ROOT
    # 设成 tmp_path/workspaces，resolve_workspaces_dir 再追加 /workspaces → 嵌套一层；
    # 改回父目录使 cfg.workspaces_dir == tmp_workspaces，auth.db 才能落到存在目录里。
    from supernova_core.utils.paths import resolve_workspaces_dir
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    assert resolve_workspaces_dir() == tmp_workspaces
    app = create_app()
    app.state.auth_store.create_user("alice", hash_password("pw123"))
    return TestClient(app)


def _csrf(c):
    return c.get("/api/auth/csrf").json()["csrf_token"]


def test_login_success(client):
    tok = _csrf(client)
    r = client.post("/api/auth/login", json={"username": "alice", "password": "pw123"}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "alice"


def test_login_wrong_password(client):
    tok = _csrf(client)
    r = client.post("/api/auth/login", json={"username": "alice", "password": "wrong"}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 401


def test_login_requires_csrf(client):
    r = client.post("/api/auth/login", json={"username": "alice", "password": "pw123"})
    assert r.status_code == 403


def test_me_after_login(client):
    tok = _csrf(client)
    client.post("/api/auth/login", json={"username": "alice", "password": "pw123"}, headers={"X-CSRF-Token": tok})
    r = client.get("/api/auth/me")
    assert r.status_code == 200 and r.json()["user"]["username"] == "alice"


def test_me_without_login_401(client):
    assert client.get("/api/auth/me").status_code == 401


def test_logout_invalidates_session(client):
    tok = _csrf(client)
    client.post("/api/auth/login", json={"username": "alice", "password": "pw123"}, headers={"X-CSRF-Token": tok})
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": tok}).status_code == 200
    assert client.get("/api/auth/me").status_code == 401
