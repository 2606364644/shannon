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
