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
    tok = client.cookies.get("sn-csrf", tok)  # login 续签 csrf，从 cookie jar 取新 token
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": tok}).status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_logout_requires_csrf(client):
    """logout 必须校验 CSRF（I1 回归：缺/错 token → 403）。"""
    tok = _csrf(client)
    client.post("/api/auth/login", json={"username": "alice", "password": "pw123"}, headers={"X-CSRF-Token": tok})
    tok = client.cookies.get("sn-csrf", tok)
    # 不带 X-CSRF-Token
    assert client.post("/api/auth/logout").status_code == 403
    # 错 token
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": "stale-invalid"}).status_code == 403
    # session 仍有效（上面两次 logout 被 403 拦下，未吊销）
    assert client.get("/api/auth/me").status_code == 200
    # 正确 token 后才真正 logout
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": tok}).status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_login_sets_secure_cookie_attributes(client):
    """sn-sid cookie 必须带 HttpOnly/SameSite=Lax/Max-Age（I3 回归：
    防止未来对 _cookie_kwargs 的误改静默丢属性）。fixture 走 HTTP
    （COOKIE_SECURE=0），故不验 Secure。"""
    tok = _csrf(client)
    r = client.post("/api/auth/login", json={"username": "alice", "password": "pw123"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    set_cookies = r.headers.get_list("set-cookie")
    sid_cookie = next(c for c in set_cookies if c.startswith("sn-sid="))
    assert "HttpOnly" in sid_cookie
    assert "SameSite=lax" in sid_cookie
    assert "Max-Age=" in sid_cookie
