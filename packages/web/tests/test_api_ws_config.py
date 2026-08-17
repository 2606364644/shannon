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

def test_get_new_ws_is_default_true(authed_client, tmp_workspaces):
    """新工作区（无 config.yaml）→ GET 返回 is_default=True（前端据此预填完整模板）。"""
    (tmp_workspaces / "ws-a").mkdir()
    r = authed_client.get("/api/workspaces/ws-a/config")
    assert r.status_code == 200
    assert r.json()["is_default"] is True


def test_get_existing_ws_is_default_false(authed_client, tmp_workspaces):
    """已保存配置的工作区 → GET 返回 is_default=False（前端显示实际配置，不预填）。"""
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": "SUPERNOVA_AI_PROVIDER=openai_compatible\n",
    }, headers={"X-CSRF-Token": tok})
    r = authed_client.get("/api/workspaces/ws-a/config")
    assert r.json()["is_default"] is False


def test_get_config_empty_ws(authed_client, tmp_workspaces):
    """没有 config.yaml 的 ws → 默认 Provider 模板，但不包含 API key。"""
    (tmp_workspaces / "ws-a").mkdir()
    r = authed_client.get("/api/workspaces/ws-a/config")
    assert r.status_code == 200
    env_text = r.json()["env_text"]
    assert "SUPERNOVA_AI_PROVIDER=openai_compatible" in env_text
    assert "SUPERNOVA_OPENAI_BASE_URL=https://llm-proxy.futuoa.com/v1" in env_text
    assert "SUPERNOVA_OPENAI_LARGE_MODEL=glm-5.2-coder" in env_text
    assert "SUPERNOVA_OPENAI_MEDIUM_MODEL=glm-5.2-coder" in env_text
    assert "SUPERNOVA_OPENAI_SMALL_MODEL=glm-5.2-coder" in env_text
    assert "SUPERNOVA_OPENAI_API_KEY" not in env_text


def test_get_config_partial_ws_does_not_use_global_provider(
    authed_client, tmp_workspaces, monkeypatch,
):
    """工作区未选 provider 时，配置展示也不从全局 provider 回落。"""
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    r = authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": "SUPERNOVA_OPENAI_BASE_URL=http://workspace.example/v1\n",
    }, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    monkeypatch.setenv("SUPERNOVA_AI_PROVIDER", "anthropic_api")

    env_text = authed_client.get("/api/workspaces/ws-a/config").json()["env_text"]

    assert "SUPERNOVA_OPENAI_BASE_URL=http://workspace.example/v1" in env_text
    assert "ANTHROPIC_BASE_URL" not in env_text


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


# ---- display 文本回显保真（保存什么就看到什么）----

def test_put_then_get_preserves_comments_and_layout(authed_client, tmp_workspaces):
    """保存后 GET/PUT 回显用户提交的原样文本：注释行、空行、ineffective/unknown 行、
    顺序全部保留（运行时仍只按 parse 出的字段生效）。"""
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    text = ("# --- 引擎与端点 ---\n"
            "SUPERNOVA_AI_PROVIDER=openai_compatible\n"
            "#SUPERNOVA_OPENAI_API_KEY=\n"
            "\n"
            "SUPERNOVA_MAX_CONCURRENT=8\n"
            "BOGUS_KEY=x\n")
    r = authed_client.put("/api/workspaces/ws-a/config", json={"env_text": text},
                          headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    # PUT 响应即回显（注释/占位/警告行原样，凭据占位行无值不打码）
    assert r.json()["env_text"] == text
    # GET 同样原样
    assert authed_client.get("/api/workspaces/ws-a/config").json()["env_text"] == text


def test_put_masks_credential_in_display_and_at_rest(authed_client, app_with_ws, tmp_workspaces):
    """提交的凭据值：回显打码 + config.yaml 落盘无明文。"""
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    text = ("SUPERNOVA_OPENAI_API_KEY=sk-secret\n"
            "GITLAB_TOKEN=glpat-secret\n"
            "SUPERNOVA_OPENAI_BASE_URL=http://x\n")
    r = authed_client.put("/api/workspaces/ws-a/config", json={"env_text": text},
                          headers={"X-CSRF-Token": tok})
    body = r.json()["env_text"]
    assert "SUPERNOVA_OPENAI_API_KEY=••••" in body
    assert "GITLAB_TOKEN=••••" in body
    assert "sk-secret" not in body and "glpat-secret" not in body
    # 落盘（display 段 + 整个 yaml）不含明文凭据；字段值走 vault 加密
    raw = (tmp_workspaces / "ws-a" / "config.yaml").read_text("utf-8")
    assert "sk-secret" not in raw and "glpat-secret" not in raw
    assert app_with_ws.state.ws_config_store.read("ws-a").provider.api_key == "sk-secret"


def test_masked_roundtrip_keeps_credential(authed_client, app_with_ws, tmp_workspaces):
    """保存 → 回显掩码 → 原文再保存：掩码触发智能保留，凭据不丢。"""
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": "SUPERNOVA_OPENAI_API_KEY=sk-orig\n",
    }, headers={"X-CSRF-Token": tok})
    echoed = authed_client.get("/api/workspaces/ws-a/config").json()["env_text"]
    assert echoed == "SUPERNOVA_OPENAI_API_KEY=••••\n"
    authed_client.put("/api/workspaces/ws-a/config", json={"env_text": echoed},
                      headers={"X-CSRF-Token": tok})
    assert app_with_ws.state.ws_config_store.read("ws-a").provider.api_key == "sk-orig"


def test_legacy_config_without_display_text_still_renders(authed_client, tmp_workspaces):
    """旧配置（yaml 无 env_text 段）→ GET 回落 render_env_text，行为不变。"""
    (tmp_workspaces / "ws-a").mkdir()
    (tmp_workspaces / "ws-a" / "config.yaml").write_text(
        "provider:\n"
        "  ai_provider: openai_compatible\n"
        "  base_url: http://legacy\n"
        "  small_model: glm-5.2-coder\n"
        "  medium_model: glm-5.2-coder\n"
        "  large_model: glm-5.2-coder\n", encoding="utf-8")
    env_text = authed_client.get("/api/workspaces/ws-a/config").json()["env_text"]
    assert "SUPERNOVA_OPENAI_BASE_URL=http://legacy" in env_text


# ---- env 段（扫描期 per-workspace 覆盖）----

def test_put_then_get_env_section_roundtrip(authed_client, app_with_ws, tmp_workspaces):
    """扫描期键写 env 段 → GET 原样回显 + store 读到 env dict。"""
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    r = authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": ("SUPERNOVA_LLM_TRACK_ENABLED=0\n"
                     "SUPERNOVA_BROWSER_ENGINE=agent-browser\n"
                     "SUPERNOVA_PRICING_OVERRIDE=p.json\n"),
    }, headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert r.json()["warnings"]["ineffective"] == []  # 扫描期键不再 ineffective

    env_text = authed_client.get("/api/workspaces/ws-a/config").json()["env_text"]
    assert "SUPERNOVA_LLM_TRACK_ENABLED=0" in env_text
    assert "SUPERNOVA_BROWSER_ENGINE=agent-browser" in env_text
    assert "SUPERNOVA_PRICING_OVERRIDE=p.json" in env_text

    store = app_with_ws.state.ws_config_store
    cfg = store.read("ws-a")
    assert cfg.env["SUPERNOVA_LLM_TRACK_ENABLED"] == "0"
    assert cfg.env["SUPERNOVA_BROWSER_ENGINE"] == "agent-browser"
    assert store.resolve_env_overrides("ws-a")["SUPERNOVA_PRICING_OVERRIDE"] == "p.json"


def test_put_env_section_full_overwrite(authed_client, tmp_workspaces):
    """env 段全量覆盖：第二次不写 LLM_TRACK → 清空。"""
    (tmp_workspaces / "ws-a").mkdir()
    tok = _csrf(authed_client)
    authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": "SUPERNOVA_LLM_TRACK_ENABLED=0\n",
    }, headers={"X-CSRF-Token": tok})
    authed_client.put("/api/workspaces/ws-a/config", json={
        "env_text": "SUPERNOVA_BROWSER_ENGINE=agent-browser\n",
    }, headers={"X-CSRF-Token": tok})
    env_text = authed_client.get("/api/workspaces/ws-a/config").json()["env_text"]
    assert "LLM_TRACK" not in env_text  # 全量覆盖清空
    assert "SUPERNOVA_BROWSER_ENGINE=agent-browser" in env_text
