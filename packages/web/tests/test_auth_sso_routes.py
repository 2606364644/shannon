# packages/web/tests/test_auth_sso_routes.py
"""SSO 端点集成测试（spec 2026-08-25 §5.2）。validateTicket 经 monkeypatch sso.validate_ticket 注入，
覆盖：config/login 302/callback 校验链全分支/JIT 幂等/cookie 属性/关闭态 404。"""
import time

import pytest
from starlette.testclient import TestClient

from supernova_web.app import create_app
from supernova_web.auth import sso as sso_mod
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def sso_client(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    # 对齐 conftest.app_with_ws 的 remount：WORKER_ROOT 指父目录使 workspaces_dir==tmp_workspaces
    from supernova_core.utils.paths import resolve_workspaces_dir
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    assert resolve_workspaces_dir() == tmp_workspaces
    monkeypatch.setenv("SUPERNOVA_WEB_SSO_ENABLED", "1")
    monkeypatch.setenv("SUPERNOVA_WEB_SSO_AUTH_DOMAIN", "codescan.test.local")
    app = create_app()
    app.state.auth_store.add_sso_whitelist("niu", "admin")
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def plain_client(tmp_workspaces, monkeypatch):  # SSO 关闭（默认 env）
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_core.utils.paths import resolve_workspaces_dir
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    assert resolve_workspaces_dir() == tmp_workspaces
    return TestClient(create_app(), follow_redirects=False)


def _payload():
    now = int(time.time())
    return {"result": 0, "code": 0, "message": "success",
            "data": {"oaToken": "tok", "oaTokenInitTime": now - 60, "oaTokenInvalidTime": now + 3600,
                     "userInfo": {"uid": 8537, "nick": "niu",
                                  "avatarUrl": "https://cdn.test/a.png"}}}


def _mock_validate(monkeypatch, payload=None, error_code=None):
    def fake(passport_base, auth_domain, ticket, *, transport=None, now=None):
        if error_code:
            raise sso_mod.SsoTicketError(error_code, "test")
        return sso_mod.parse_validate_response(payload or _payload(), time.time())
    monkeypatch.setattr(sso_mod, "validate_ticket", fake)


# ---------- config / login ----------

def test_sso_config_disabled_by_default(plain_client):
    assert plain_client.get("/api/auth/sso/config").json() == {"enabled": False}


def test_sso_login_disabled_404(plain_client):
    assert plain_client.get("/api/auth/sso/login", follow_redirects=False).status_code == 404


def test_sso_config_enabled(sso_client):
    assert sso_client.get("/api/auth/sso/config").json() == {"enabled": True}


def test_sso_login_redirect_url_encoding(sso_client):
    from urllib.parse import quote
    r = sso_client.get("/api/auth/sso/login", params={"next": "/p/ws-1"}, follow_redirects=False)
    assert r.status_code == 302
    callback = "https://codescan.test.local/api/auth/sso/callback?next=" + quote("/p/ws-1", safe="")
    assert r.headers["location"] == ("https://passport.futuoa.com/site/login.html?returnUrl="
                                     + quote(callback, safe=""))


def test_sso_login_unsafe_next_falls_back(sso_client):
    r = sso_client.get("/api/auth/sso/login", params={"next": "//evil.com"}, follow_redirects=False)
    # brief 原断言 "next=%2F" 与 Task 3 build_passport_login_url 的双层编码不一致
    # （returnUrl 整体再编码：next=/ → next%3D%252F，见 test_auth_sso.py 双编码用例）。
    # 端点行为不变：unsafe next 被 safe_next 回落成 "/"，evil.com 不得外泄进 returnUrl。
    assert "next%3D%252F" in r.headers["location"]
    assert "evil.com" not in r.headers["location"]


# ---------- callback ----------

def test_callback_success_creates_user_session_cookie(sso_client, monkeypatch):
    _mock_validate(monkeypatch)
    r = sso_client.get("/api/auth/sso/callback",
                       params={"AUTH_TICKET": "T-1", "next": "/p/ws-1"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/p/ws-1"
    sid_cookie = next(c for c in r.headers.get_list("set-cookie") if c.startswith("sn-sid="))
    assert "HttpOnly" in sid_cookie and "SameSite=lax" in sid_cookie
    assert "Max-Age=86400" in sid_cookie  # SSO 24h
    # JIT 建户 + avatar + provider
    store = sso_client.app.state.auth_store
    u = store.get_user_by_username("niu")
    assert u is not None and u.auth_provider == "sso"
    assert u.avatar_url == "https://cdn.test/a.png"
    # 会话生效
    me = sso_client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["user"]["username"] == "niu"


def test_callback_jit_idempotent_and_avatar_refresh(sso_client, monkeypatch):
    _mock_validate(monkeypatch)
    for i, url in enumerate(["https://cdn.test/a.png", "https://cdn.test/b.png"]):
        _mock_validate(monkeypatch, payload={**_payload(), "data": {
            **_payload()["data"], "userInfo": {"uid": 8537, "nick": "niu", "avatarUrl": url}}})
        sso_client.get("/api/auth/sso/callback", params={"AUTH_TICKET": f"T-{i}"}, follow_redirects=False)
    store = sso_client.app.state.auth_store
    users = [u for u in store.list_all_users() if u.username == "niu"]
    assert len(users) == 1 and users[0].avatar_url == "https://cdn.test/b.png"


def test_callback_not_whitelisted(sso_client, monkeypatch):
    _mock_validate(monkeypatch, payload={**_payload(), "data": {
        **_payload()["data"], "userInfo": {"uid": 1, "nick": "outsider", "avatarUrl": None}}})
    r = sso_client.get("/api/auth/sso/callback", params={"AUTH_TICKET": "T-out"},
                       follow_redirects=False)
    assert r.headers["location"] == "/login?sso_error=not_whitelisted"
    assert store_has_no_user(sso_client, "outsider") is True


def store_has_no_user(client, name):
    return client.app.state.auth_store.get_user_by_username(name) is None


def test_callback_replay_rejected(sso_client, monkeypatch):
    _mock_validate(monkeypatch)
    sso_client.get("/api/auth/sso/callback", params={"AUTH_TICKET": "T-same"}, follow_redirects=False)
    r2 = sso_client.get("/api/auth/sso/callback", params={"AUTH_TICKET": "T-same"}, follow_redirects=False)
    assert r2.headers["location"] == "/login?sso_error=replayed_ticket"


def test_callback_missing_ticket(sso_client):
    r = sso_client.get("/api/auth/sso/callback", params={"next": "/"}, follow_redirects=False)
    assert r.headers["location"] == "/login?sso_error=missing_ticket"


def test_callback_ticket_error_codes(sso_client, monkeypatch):
    for code in ("token_expired", "upstream_error", "invalid_response", "missing_nick"):
        _mock_validate(monkeypatch, error_code=code)
        r = sso_client.get("/api/auth/sso/callback", params={"AUTH_TICKET": f"t-{code}"},
                           follow_redirects=False)
        assert r.headers["location"] == f"/login?sso_error={code}"


def test_callback_disabled_404(plain_client):
    assert plain_client.get("/api/auth/sso/callback", params={"AUTH_TICKET": "T"},
                            follow_redirects=False).status_code == 404
