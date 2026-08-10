"""ws config API：env 文本契约（GET 渲染 env_text / PUT parse+全量覆盖+掩码保留+warnings）。

spec: docs/superpowers/specs/2026-08-10-ws-config-env-textarea-design.md
"""
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
    return app


def _login(app, username):
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": username, "password": "p"},
           headers={"X-CSRF-Token": tok})
    return c


def _csrf(c):
    return c.get("/api/auth/csrf").json()["csrf_token"]


# ---- GET / 基本读写 ----

def test_get_config_empty_ws(authed_client, tmp_workspaces):
    """空 ws → 空 env 文本。"""
    (tmp_workspaces / "ws-a").mkdir()
    r = authed_client.get("/api/workspaces/ws-a/config")
    assert r.status_code == 200
    assert r.json()["env_text"] == ""


def test_put_then_get_masks_api_key(authed_client, tmp_workspaces):
    """写入 → GET 凭据掩码、非凭据明文。"""
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    r = authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": ("SUPERNOVA_AI_PROVIDER=openai_compatible\n"
                     "SUPERNOVA_OPENAI_API_KEY=sk-secret\n"
                     "SUPERNOVA_OPENAI_BASE_URL=http://x\n"),
    }, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    env_text = authed_client.get("/api/workspaces/ws-a/config").json()["env_text"]
    assert "SUPERNOVA_OPENAI_API_KEY=••••" in env_text
    assert "sk-secret" not in env_text
    assert "SUPERNOVA_OPENAI_BASE_URL=http://x" in env_text
    assert "SUPERNOVA_AI_PROVIDER=openai_compatible" in env_text


# ---- 全量覆盖 + 凭据智能保留 ----

def test_put_full_overwrite_clears_unset_fields(authed_client, tmp_workspaces):
    """全量覆盖:第二次没写 base_url → 清空。"""
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": "SUPERNOVA_AI_PROVIDER=openai_compatible\nSUPERNOVA_OPENAI_BASE_URL=http://x\n",
    }, headers={"X-CSRF-Token": tok})
    authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": "SUPERNOVA_AI_PROVIDER=openai_compatible\n",
    }, headers={"X-CSRF-Token": tok})
    env_text = authed_client.get("/api/workspaces/ws-a/config").json()["env_text"]
    assert "BASE_URL" not in env_text


def test_put_masked_credential_kept(authed_client, app_with_ws, tmp_workspaces):
    """掩码=不改:api_key=•••• + 改 base_url → api_key 保留原值(读 store 明文)。"""
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": ("SUPERNOVA_AI_PROVIDER=openai_compatible\n"
                     "SUPERNOVA_OPENAI_API_KEY=sk-orig\n"
                     "SUPERNOVA_OPENAI_BASE_URL=http://x\n"),
    }, headers={"X-CSRF-Token": tok})
    authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": ("SUPERNOVA_AI_PROVIDER=openai_compatible\n"
                     "SUPERNOVA_OPENAI_API_KEY=••••\n"
                     "SUPERNOVA_OPENAI_BASE_URL=http://y\n"),
    }, headers={"X-CSRF-Token": tok})
    store = app_with_ws.state.ws_config_store
    cfg = store.read("ws-a")
    assert cfg.provider.api_key == "sk-orig"   # 保留
    assert cfg.provider.base_url == "http://y"  # 更新


def test_put_deleted_credential_line_clears(authed_client, app_with_ws, tmp_workspaces):
    """删凭据行 → 清空(全量覆盖语义)。"""
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": "SUPERNOVA_AI_PROVIDER=openai_compatible\nSUPERNOVA_OPENAI_API_KEY=sk-orig\n",
    }, headers={"X-CSRF-Token": tok})
    authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": "SUPERNOVA_AI_PROVIDER=openai_compatible\n",
    }, headers={"X-CSRF-Token": tok})
    assert app_with_ws.state.ws_config_store.read("ws-a").provider.api_key is None


# ---- warnings（进程级 / 未知 key，不阻塞）----

def test_put_returns_warnings(authed_client, tmp_workspaces):
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    r = authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": ("SUPERNOVA_AI_PROVIDER=openai_compatible\n"
                     "SUPERNOVA_MAX_CONCURRENT=8\n"
                     "BOGUS_KEY=x\n"),
    }, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    w = r.json()["warnings"]
    assert w["ineffective"] == ["SUPERNOVA_MAX_CONCURRENT"]
    assert w["unknown"] == ["BOGUS_KEY"]


# ---- 422 ----

def test_put_invalid_env_line_422(authed_client, tmp_workspaces):
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    r = authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": "NO_EQUALS_HERE\n",
    }, headers={"X-CSRF-Token": tok})
    assert r.status_code == 422


def test_put_unknown_provider_422(authed_client, tmp_workspaces):
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    r = authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": "SUPERNOVA_AI_PROVIDER=bogus\n",
    }, headers={"X-CSRF-Token": tok})
    assert r.status_code == 422


# ---- 鉴权 ----

def test_get_non_member_403(_app):
    """carol 非 ws-a 成员 → GET 403。"""
    c = _login(_app, "carol")
    assert c.get("/api/workspaces/ws-a/config").status_code == 403


def test_put_non_manager_403(_app):
    """bob 是 member（非 manager）→ PUT 403。"""
    c = _login(_app, "bob")
    tok = _csrf(c)
    r = c.put("/api/workspaces/ws-a/config",
              json={"env_text": "SUPERNOVA_AI_PROVIDER=openai_compatible\n"},
              headers={"X-CSRF-Token": tok})
    assert r.status_code == 403


# ---- git 段 ----

def test_put_then_get_masks_gitlab_token(authed_client, tmp_workspaces):
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    r = authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": "GITLAB_USER=bot-a\nGITLAB_TOKEN=glpat-secret\n",
    }, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    env_text = authed_client.get("/api/workspaces/ws-a/config").json()["env_text"]
    assert "GITLAB_USER=bot-a" in env_text
    assert "GITLAB_TOKEN=••••" in env_text
    assert "glpat-secret" not in env_text
