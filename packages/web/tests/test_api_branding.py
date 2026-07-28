"""品牌名管理 API:GET(任意登录角色) / PUT(admin-only);system-status 解析优先级。"""
from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def app(tmp_path, monkeypatch):
    # 默认不设 SUPERNOVA_WEB_BRAND_NAME → 回落 "Supernova"(与多数默认用例一致)。
    return _build_app(tmp_path, monkeypatch)


@pytest.fixture
def app_with_env_brand(tmp_path, monkeypatch):
    # env 在 app 创建前就绪(WebConfig.__init__ 读 env),供优先级用例。
    monkeypatch.setenv("SUPERNOVA_WEB_BRAND_NAME", "EnvBrand")
    return _build_app(tmp_path, monkeypatch)


def _login(app, username, role):
    app.state.auth_store.create_user(username, hash_password(f"{username}-pw"), role=role)
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.post(
        "/api/auth/login",
        json={"username": username, "password": f"{username}-pw"},
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 200
    return c


@pytest.fixture
def admin_client(app):
    return _login(app, "admin1", "admin")


@pytest.fixture
def admin_client_env(app_with_env_brand):
    return _login(app_with_env_brand, "admin2", "admin")


@pytest.fixture
def user_client(app):
    return _login(app, "alice", "user")


# ---- GET /api/branding ----


def test_get_branding_default_none(app, admin_client):
    r = admin_client.get("/api/branding")
    assert r.status_code == 200
    assert r.json() == {"brand_name": None}


def test_get_branding_unauth_redirects(app):
    # 未登录:GET 受 _require_auth 保护 → 401
    c = TestClient(app)
    assert c.get("/api/branding").status_code == 401


# ---- PUT /api/branding ----


def test_put_branding_admin_sets_and_persists(admin_client, app):
    tok = admin_client.cookies  # csrf 已在 header 里;重新取一次写操作的 token
    csrf = admin_client.get("/api/auth/csrf").json()["csrf_token"]
    r = admin_client.put(
        "/api/branding",
        json={"brand_name": "Acme Sec"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert r.json()["brand_name"] == "Acme Sec"
    assert r.json()["effective"] == "Acme Sec"
    # system-status 立即反映新名
    assert admin_client.get("/api/system-status").json()["brand_name"] == "Acme Sec"
    # 落盘文件
    from supernova_core.utils.paths import resolve_workspaces_dir
    data = json.loads((resolve_workspaces_dir() / "branding.json").read_text(encoding="utf-8"))
    assert data["brand_name"] == "Acme Sec"


def test_put_branding_trims(admin_client):
    csrf = admin_client.get("/api/auth/csrf").json()["csrf_token"]
    r = admin_client.put("/api/branding", json={"brand_name": "  Padded  "}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert r.json()["brand_name"] == "Padded"
    assert r.json()["effective"] == "Padded"


def test_put_branding_clear_with_null(admin_client):
    csrf = admin_client.get("/api/auth/csrf").json()["csrf_token"]
    admin_client.put("/api/branding", json={"brand_name": "Acme"}, headers={"X-CSRF-Token": csrf})
    r = admin_client.put("/api/branding", json={"brand_name": None}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert r.json()["brand_name"] is None
    # 清除后 effective 回落到默认 Supernova(env 未设)
    assert r.json()["effective"] == "Supernova"
    # 清除后 system-status 回落 env/default("Supernova")
    assert admin_client.get("/api/system-status").json()["brand_name"] == "Supernova"


def test_put_branding_rejects_empty_string(admin_client):
    csrf = admin_client.get("/api/auth/csrf").json()["csrf_token"]
    r = admin_client.put("/api/branding", json={"brand_name": "   "}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_put_branding_rejects_too_long(admin_client):
    csrf = admin_client.get("/api/auth/csrf").json()["csrf_token"]
    r = admin_client.put(
        "/api/branding",
        json={"brand_name": "x" * 33},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_put_branding_non_admin_forbidden(user_client):
    csrf = user_client.get("/api/auth/csrf").json()["csrf_token"]
    r = user_client.put(
        "/api/branding",
        json={"brand_name": "Hacker"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 403
    # 未改动
    assert user_client.get("/api/system-status").json()["brand_name"] == "Supernova"


def test_put_branding_unauth(app):
    c = TestClient(app)
    csrf = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.put("/api/branding", json={"brand_name": "X"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 401


# ---- system-status 解析优先级 ----


def _build_app(tmp_path, monkeypatch):
    """建 app:env 在创建前就绪(WebConfig.__init__ 读 env)。"""
    (tmp_path / "workspaces").mkdir()
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    return create_app()


def test_resolution_override_beats_env(admin_client_env):
    # env=EnvBrand,设覆盖 → override 优先。
    csrf = admin_client_env.get("/api/auth/csrf").json()["csrf_token"]
    admin_client_env.put("/api/branding", json={"brand_name": "Override"}, headers={"X-CSRF-Token": csrf})
    assert admin_client_env.get("/api/system-status").json()["brand_name"] == "Override"


def test_resolution_env_when_no_override(admin_client_env):
    # env=EnvBrand,无覆盖 → env 生效。
    assert admin_client_env.get("/api/system-status").json()["brand_name"] == "EnvBrand"


def test_resolution_default_when_nothing_set(app, admin_client):
    assert admin_client.get("/api/system-status").json()["brand_name"] == "Supernova"
