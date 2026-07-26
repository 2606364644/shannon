"""P3c 阶段 2：ws config API（GET 脱敏 / PUT 写入 + 鉴权）。"""
import pytest
from starlette.testclient import TestClient
from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def _app(tmp_workspaces, monkeypatch):
    """多用户 fixture：alice=ws-a manager, bob=ws-a member, carol=非成员。"""
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    app = create_app()
    st = app.state.auth_store
    st.create_user("alice", hash_password("p"))
    st.create_user("bob", hash_password("p"))
    st.create_user("carol", hash_password("p"))
    (app.state.config.workspaces_dir / "ws-a").mkdir()
    st.add_workspace_member("ws-a", st.get_user_by_username("alice").id, "manager")
    st.add_workspace_member("ws-a", st.get_user_by_username("bob").id, "member")
    # carol 不加入 ws-a（非成员）
    return app


def _login(app, username):
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": username, "password": "p"},
           headers={"X-CSRF-Token": tok})
    return c


def _csrf(c):
    return c.get("/api/auth/csrf").json()["csrf_token"]


# ---- 基本用例（admin，authed_client 直通）----

def test_get_config_empty_ws(authed_client, tmp_workspaces):
    """空 ws → 全 None（脱敏 api_key=None）。"""
    (tmp_workspaces / "ws-a").mkdir()
    r = authed_client.get("/api/workspaces/ws-a/config")
    assert r.status_code == 200
    prov = r.json()["provider"]
    assert prov["api_key"] is None
    assert prov["ai_provider"] is None


def test_put_then_get_masks_api_key(authed_client, tmp_workspaces):
    """写入 api_key → GET 返 '••••'（脱敏，非明文）。"""
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    r = authed_client.put("/api/workspaces/ws-a/config", json={
        "provider": {"ai_provider": "openai_compatible", "api_key": "sk-secret", "base_url": "http://x"}
    }, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    g = authed_client.get("/api/workspaces/ws-a/config").json()["provider"]
    assert g["api_key"] == "••••"        # 脱敏
    assert g["ai_provider"] == "openai_compatible"
    assert g["base_url"] == "http://x"


def test_put_empty_api_key_keeps_existing(authed_client, tmp_workspaces):
    """api_key 空串/缺省 = 不改（保留原值）。"""
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    authed_client.put("/api/workspaces/ws-a/config",
                      json={"provider": {"api_key": "sk-orig"}},
                      headers={"X-CSRF-Token": tok})
    authed_client.put("/api/workspaces/ws-a/config",
                      json={"provider": {"api_key": "", "model": "m"}},
                      headers={"X-CSRF-Token": tok})
    g = authed_client.get("/api/workspaces/ws-a/config").json()["provider"]
    assert g["api_key"] == "••••"
    assert g["model"] == "m"


def test_put_unknown_provider_422(authed_client, tmp_workspaces):
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    r = authed_client.put("/api/workspaces/ws-a/config",
                          json={"provider": {"ai_provider": "bogus"}},
                          headers={"X-CSRF-Token": tok})
    assert r.status_code == 422


# ---- 鉴权用例（多用户）----

def test_get_non_member_403(_app):
    """carol 非 ws-a 成员 → GET 403。"""
    c = _login(_app, "carol")
    r = c.get("/api/workspaces/ws-a/config")
    assert r.status_code == 403


def test_put_non_manager_403(_app):
    """bob 是 ws-a 的 member（非 manager）→ PUT 403。"""
    c = _login(_app, "bob")
    tok = _csrf(c)
    r = c.put("/api/workspaces/ws-a/config",
              json={"provider": {"ai_provider": "openai_compatible"}},
              headers={"X-CSRF-Token": tok})
    assert r.status_code == 403
