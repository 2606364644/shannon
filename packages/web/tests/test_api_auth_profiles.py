"""auth_profiles CRUD API + 权限(workspace_member 看 / workspace_manager 改删)。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from supernova_web.api import auth_profiles
from supernova_web.auth.dependencies import current_user, workspace_member, workspace_manager
from supernova_web.components.auth_profile_store import AuthProfileStore
from supernova_web.components.credential_vault import CredentialVault


class _U:
    role = "admin"
    id = 1


def _client(tmp_path):
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    app = FastAPI()
    app.state.auth_profile_store = store
    app.state.scan_manager = MagicMock()
    app.include_router(auth_profiles.router)
    # 测试绕过鉴权(admin 短路,但 dependency_overrides 更稳)
    app.dependency_overrides[current_user] = lambda: _U()
    app.dependency_overrides[workspace_member] = lambda: _U()
    app.dependency_overrides[workspace_manager] = lambda: _U()
    return TestClient(app), store


def test_create_list_get_delete(tmp_path):
    c, _store = _client(tmp_path)
    body = {"name": "NG", "login_url": "http://t/", "login_type": "form",
            "credentials": [{"role": "admin", "username": "admin", "password": "pw"}]}
    r = c.post("/api/workspaces/ws1/auth-profiles", json=body)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    # list 脱敏
    lst = c.get("/api/workspaces/ws1/auth-profiles").json()
    assert lst[0]["name"] == "NG"
    assert lst[0]["credentials"][0]["password"] == "••••"
    # get 脱敏
    assert c.get(f"/api/workspaces/ws1/auth-profiles/{pid}").status_code == 200
    # delete
    assert c.delete(f"/api/workspaces/ws1/auth-profiles/{pid}").status_code == 200
    assert c.get("/api/workspaces/ws1/auth-profiles").json() == []


def test_put_empty_secret_keeps_existing(tmp_path):
    c, store = _client(tmp_path)
    pid = c.post("/api/workspaces/ws1/auth-profiles", json={
        "name": "NG", "login_url": "http://t/", "login_type": "form",
        "credentials": [{"role": "admin", "username": "admin", "password": "pw"}]}).json()["id"]
    cred_id = c.get(f"/api/workspaces/ws1/auth-profiles/{pid}").json()["credentials"][0]["id"]
    # PUT 空串 password = 不改
    c.put(f"/api/workspaces/ws1/auth-profiles/{pid}", json={
        "name": "NG2", "login_url": "http://t/", "login_type": "form",
        "credentials": [{"id": cred_id, "role": "admin", "username": "admin", "password": ""}]})
    cred = store.read("ws1")[0].credentials[0]
    assert cred.password == "pw"  # 保留


@pytest.mark.asyncio
async def test_test_endpoint_starts_workflow(tmp_path, monkeypatch):
    c, store = _client(tmp_path)
    # 预置档案
    pid = c.post("/api/workspaces/ws1/auth-profiles", json={
        "name": "NG", "login_url": "http://t/", "login_type": "form",
        "credentials": [{"role": "admin", "username": "admin", "password": "pw"}]}).json()["id"]
    cred_id = store.read("ws1")[0].credentials[0].id
    sm = c.app.state.scan_manager
    # Task 6 reconciliation: start_auth_validation 返 dict {workflow_id, probe_dir}
    sm.start_auth_validation = AsyncMock(
        return_value={"workflow_id": "wf-xyz", "probe_dir": "/p/probe"})
    r = c.post(f"/api/workspaces/ws1/auth-profiles/{pid}/credentials/{cred_id}/test")
    assert r.status_code == 200, r.text
    assert r.json()["workflow_id"] == "wf-xyz"
    sm.start_auth_validation.assert_awaited_once_with("ws1", pid, cred_id)


def test_get_profile_does_single_masked_read(tmp_path, monkeypatch):
    """IMPORTANT 1 回归:get_profile 合并成单次 read_masked 查找,防两次读之间档案被删
    致 masked=None.model_dump() → 500(get 与 read_masked 之间的 TOCTOU race)。
    """
    c, store = _client(tmp_path)
    pid = c.post("/api/workspaces/ws1/auth-profiles", json={
        "name": "NG", "login_url": "http://t/", "login_type": "form",
        "credentials": [{"role": "admin", "username": "admin", "password": "pw"}]}).json()["id"]
    # spy read_masked:确认 get 路径只调一次(合并后),且不调 get()
    call_count = {"read_masked": 0, "get": 0}
    orig_read_masked = store.read_masked
    orig_get = store.get

    def counting_read_masked(ws):
        call_count["read_masked"] += 1
        return orig_read_masked(ws)

    def counting_get(ws, pid):
        call_count["get"] += 1
        return orig_get(ws, pid)

    monkeypatch.setattr(store, "read_masked", counting_read_masked)
    monkeypatch.setattr(store, "get", counting_get)
    r = c.get(f"/api/workspaces/ws1/auth-profiles/{pid}")
    assert r.status_code == 200, r.text
    assert call_count["read_masked"] == 1  # 单次合并读
    assert call_count["get"] == 0  # 不再走分离的 get()
    # 不存在的 pid → 404(不 500)
    assert c.get("/api/workspaces/ws1/auth-profiles/nope").status_code == 404


def test_update_profile_new_credential_with_unknown_key_does_not_500(tmp_path):
    """IMPORTANT 2 回归:update_profile 新增 credential 时未知客户端键不再致
    pydantic ValidationError → 500(allow-list 过滤后忽略未知键,凭据正常建)。
    """
    c, store = _client(tmp_path)
    pid = c.post("/api/workspaces/ws1/auth-profiles", json={
        "name": "NG", "login_url": "http://t/", "login_type": "form",
        "credentials": [{"role": "admin", "username": "admin", "password": "pw"}]}).json()["id"]
    # 带 __class__ / 任意未知键的新 credential:不再 500
    r = c.put(f"/api/workspaces/ws1/auth-profiles/{pid}", json={
        "name": "NG", "login_url": "http://t/", "login_type": "form",
        "credentials": [{"role": "user", "username": "u2", "password": "pw2",
                         "__class__": "malicious", "unknown_key": "drop-me"}]})
    assert r.status_code == 200, r.text
    # 新 credential 被建(未知键被 allow-list 过滤)
    prof = store.read("ws1")[0]
    roles = {c.role for c in prof.credentials}
    assert "user" in roles
