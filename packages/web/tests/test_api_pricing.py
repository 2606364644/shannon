"""全局定价 API：GET 全员 / PUT·DELETE admin；保存即接管（快照压过 profile env）。

spec: docs/superpowers/specs/2026-08-28-global-pricing-console-design.md §4.2
"""
from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture(autouse=True)
def _clean_pricing_env(monkeypatch):
    monkeypatch.delenv("SUPERNOVA_PRICING_OVERRIDE", raising=False)
    monkeypatch.delenv("SUPERNOVA_GLOBAL_PRICING", raising=False)


@pytest.fixture
def app(tmp_path, monkeypatch):
    (tmp_path / "workspaces").mkdir()
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    return create_app()


def _login(app, username, role):
    app.state.auth_store.create_user(username, hash_password(f"{username}-pw"), role=role)
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.post("/api/auth/login", json={"username": username, "password": f"{username}-pw"},
               headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    return c


@pytest.fixture
def admin_client(app):
    return _login(app, "admin1", "admin")


@pytest.fixture
def user_client(app):
    return _login(app, "alice", "user")


def _tiers(i=1.0, o=2.0, cr=0.5, cc=0.0):
    return {"input": i, "output": o, "cache_read": cr, "cache_creation": cc}


def _put(c, body):
    csrf = c.get("/api/auth/csrf").json()["csrf_token"]
    return c.put("/api/pricing", json=body, headers={"X-CSRF-Token": csrf})


# ---- GET /api/pricing ----


def test_get_pricing_all_roles_can_read(user_client):
    r = user_client.get("/api/pricing")
    assert r.status_code == 200
    body = r.json()
    assert body["currency"] == "CNY"
    assert body["has_global_table"] is False
    assert body["table_corrupt"] is False
    assert body["builtin_defaults"]["glm-5.2"]["input"] == 8.0
    by_model = {m["model"]: m for m in body["models"]}
    assert by_model["glm-5.2"]["source"] == "builtin"
    assert by_model["glm-5.2"]["prices"]["output"] == 28.0
    assert set(by_model["glm-5.2"]["prices"]) == {"input", "output", "cache_read", "cache_creation"}
    assert "workspace" not in {m["source"] for m in body["models"]}  # 全局视角


def test_get_pricing_unauth_401(app):
    assert TestClient(app).get("/api/pricing").status_code == 401


# ---- PUT /api/pricing ----


def test_put_pricing_admin_writes_snapshot(admin_client, app):
    body = {"currency": "CNY", "models": {
        "glm-5.2": _tiers(9, 30, 3, 0), "extra-model": _tiers(1, 1, 1, 1)}}
    r = _put(admin_client, body)
    assert r.status_code == 200
    # 落盘 = 完整快照（归一 key）
    from supernova_core.utils.paths import resolve_workspaces_dir
    data = json.loads((resolve_workspaces_dir() / "pricing.json").read_text("utf-8"))
    assert data["currency"] == "CNY"
    assert set(data["models"]) == {"glm-5.2", "extra-model"}
    # GET 反映：接管（source=global）+ builtin 不在快照里的行不再出现
    g = admin_client.get("/api/pricing").json()
    assert g["has_global_table"] is True
    by_model = {m["model"]: m for m in g["models"]}
    assert by_model["glm-5.2"]["source"] == "global"
    assert by_model["glm-5.2"]["prices"]["input"] == 9.0
    # builtin 打底（core _pricing 同构）：快照未覆盖的 builtin 行保持 builtin 价兜底
    assert by_model["glm-4.5-air"]["source"] == "builtin"


def test_put_pricing_snapshot_beats_profile_env(admin_client, app, tmp_path, monkeypatch):
    # profile env 层存在时，PUT 后 global 压过它（界面接管语义）
    env_p = tmp_path / "env.json"
    env_p.write_text(json.dumps({"currency": "USD", "models": {"glm-5.2": _tiers(1, 1, 1, 1)}}), "utf-8")
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", str(env_p))
    r = _put(admin_client, {"currency": "CNY", "models": {"glm-5.2": _tiers(5, 5, 5, 5)}})
    assert r.status_code == 200
    g = admin_client.get("/api/pricing").json()
    by_model = {m["model"]: m for m in g["models"]}
    assert by_model["glm-5.2"]["source"] == "global"
    assert g["currency"] == "CNY"


def test_put_pricing_non_admin_403(user_client, app):
    r = _put(user_client, {"currency": "CNY", "models": {"m": _tiers()}})
    assert r.status_code == 403
    assert not (app.state.config.workspaces_dir / "pricing.json").exists()


@pytest.mark.parametrize("body,detail_part", [
    ({"currency": "EUR", "models": {"m": _tiers()}}, "currency"),
    ({"currency": "CNY", "models": {}}, "非空"),
    ({"currency": "CNY", "models": {"glm-5.2": _tiers(), "GLM-5.2[1m]": _tiers()}}, "冲突"),
    ({"currency": "CNY", "models": {"m": _tiers(-1, 2, 0, 0)}}, "≥ 0"),
    ({"currency": "CNY", "models": {"m": {"input": 1, "output": 2, "cache_read": 0}}}, "cache_creation"),
])
def test_put_pricing_validation_400(admin_client, body, detail_part):
    r = _put(admin_client, body)
    assert r.status_code == 400
    assert detail_part in r.json()["detail"]


# ---- DELETE /api/pricing ----


def test_delete_pricing_admin_reverts_and_idempotent(admin_client):
    _put(admin_client, {"currency": "USD", "models": {"m": _tiers()}})
    csrf = admin_client.get("/api/auth/csrf").json()["csrf_token"]
    r = admin_client.delete("/api/pricing", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    g = admin_client.get("/api/pricing").json()
    assert g["has_global_table"] is False
    by_model = {m["model"]: m for m in g["models"]}
    assert by_model["glm-5.2"]["source"] == "builtin"   # 回落 builtin
    assert g["currency"] == "CNY"
    # 幂等
    r2 = admin_client.delete("/api/pricing", headers={"X-CSRF-Token": csrf})
    assert r2.status_code == 200


def test_delete_pricing_non_admin_403(user_client):
    csrf = user_client.get("/api/auth/csrf").json()["csrf_token"]
    assert user_client.delete("/api/pricing", headers={"X-CSRF-Token": csrf}).status_code == 403


# ---- 损坏文件 ----


def test_get_pricing_table_corrupt_flag(admin_client, app):
    (app.state.config.workspaces_dir / "pricing.json").write_text("bad{", encoding="utf-8")
    g = admin_client.get("/api/pricing").json()
    assert g["table_corrupt"] is True
    assert g["has_global_table"] is True    # 文件在（损坏）≠未接管；层按空处理
    by_model = {m["model"]: m for m in g["models"]}
    assert by_model["glm-5.2"]["source"] == "builtin"


# ---- startup 注入 SUPERNOVA_GLOBAL_PRICING ----


def test_create_app_injects_global_pricing_env(tmp_path, monkeypatch):
    (tmp_path / "workspaces").mkdir()
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    monkeypatch.delenv("SUPERNOVA_GLOBAL_PRICING", raising=False)
    app = create_app()
    import os
    assert os.environ["SUPERNOVA_GLOBAL_PRICING"] == str(
        app.state.config.workspaces_dir / "pricing.json")
    # worker 由 web spawn 继承 env → core _pricing 的 global 层可读到该路径


def test_create_app_respects_existing_global_pricing_env(tmp_path, monkeypatch):
    (tmp_path / "workspaces").mkdir()
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    monkeypatch.setenv("SUPERNOVA_GLOBAL_PRICING", "/custom/pricing.json")
    create_app()
    import os
    assert os.environ["SUPERNOVA_GLOBAL_PRICING"] == "/custom/pricing.json"  # setdefault 不覆盖
