"""host_profiles CRUD API + 权限(workspace_member 看 / workspace_manager 改删)。

镜像 test_api_auth_profiles.py 的 _client 范式(MINIMAL FastAPI + dependency_overrides
+ sync TestClient,NOT create_app/ASGITransport —— 后者该 repo 不支持)。

覆盖:
- list-empty / create+list+get+delete
- name 唯一性 (422)
- 系统档案 PUT/DELETE → 403
- fork (200 / 409 / 422 / 404)
- parse (preview,no persist) + 失败 → 422
- refresh (按 source_url 更新 mappings)
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from supernova_web.api import host_profiles
from supernova_web.auth.dependencies import current_user, workspace_member, workspace_manager
from supernova_web.components.host_profile_store import (
    HostProfileStore, HostProfile, HostMapping,
)


class _U:
    role = "admin"
    id = 1


def _client(tmp_path):
    """MINIMAL FastAPI + 只挂 host_profiles.router + dependency_overrides 绕鉴权。

    对齐 test_api_auth_profiles._client 范式。返回 (TestClient, store)。"""
    store = HostProfileStore(tmp_path)
    app = FastAPI()
    app.state.host_profile_store = store
    app.state.scan_manager = MagicMock()
    app.include_router(host_profiles.router)
    app.dependency_overrides[current_user] = lambda: _U()
    app.dependency_overrides[workspace_member] = lambda: _U()
    app.dependency_overrides[workspace_manager] = lambda: _U()
    return TestClient(app), store


# ---------------------------------------------------------------------------
# list-empty / create + list + get + delete
# ---------------------------------------------------------------------------

def test_list_empty(tmp_path):
    c, _store = _client(tmp_path)
    r = c.get("/api/workspaces/ws1/host-profiles")
    assert r.status_code == 200
    assert r.json() == []


def test_create_list_get_delete(tmp_path):
    c, _store = _client(tmp_path)
    body = {"name": "华南",
            "mappings": [{"ip": "10.0.0.1", "host": "x.test"}]}
    r = c.post("/api/workspaces/ws1/host-profiles", json=body)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert pid.startswith("host_")
    # list 有一条
    lst = c.get("/api/workspaces/ws1/host-profiles").json()
    assert len(lst) == 1
    assert lst[0]["name"] == "华南"
    # host profile 不脱敏(明文 IP/domain)
    assert lst[0]["mappings"][0]["ip"] == "10.0.0.1"
    # get 单个
    g = c.get(f"/api/workspaces/ws1/host-profiles/{pid}")
    assert g.status_code == 200
    assert g.json()["name"] == "华南"
    # delete
    assert c.delete(f"/api/workspaces/ws1/host-profiles/{pid}").status_code == 200
    assert c.get("/api/workspaces/ws1/host-profiles").json() == []


def test_get_unknown_returns_404(tmp_path):
    c, _store = _client(tmp_path)
    r = c.get("/api/workspaces/ws1/host-profiles/host_nope")
    assert r.status_code == 404


def test_delete_unknown_returns_404(tmp_path):
    c, _store = _client(tmp_path)
    r = c.delete("/api/workspaces/ws1/host-profiles/host_nope")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# name 唯一性:create 时 ws 内重名 → 422
# ---------------------------------------------------------------------------

def test_create_duplicate_name_is_422(tmp_path):
    c, _store = _client(tmp_path)
    body = {"name": "dup", "mappings": []}
    assert c.post("/api/workspaces/ws1/host-profiles", json=body).status_code == 200
    r = c.post("/api/workspaces/ws1/host-profiles", json=body)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# PUT:update name/source_url/mappings + 404/403 守卫
# ---------------------------------------------------------------------------

def test_update_profile_fields(tmp_path):
    c, store = _client(tmp_path)
    pid = c.post("/api/workspaces/ws1/host-profiles", json={
        "name": "n1", "mappings": [{"ip": "1.1.1.1", "host": "a.test"}],
    }).json()["id"]
    r = c.put(f"/api/workspaces/ws1/host-profiles/{pid}", json={
        "name": "n2", "source_url": "https://h.test/get?id=1",
        "mappings": [{"ip": "2.2.2.2", "host": "b.test"}],
    })
    assert r.status_code == 200, r.text
    prof = store.get("ws1", pid)
    assert prof.name == "n2"
    assert prof.source_url == "https://h.test/get?id=1"
    assert [m.host for m in prof.mappings] == ["b.test"]


def test_update_unknown_returns_404(tmp_path):
    c, _store = _client(tmp_path)
    r = c.put("/api/workspaces/ws1/host-profiles/host_nope",
              json={"name": "x", "mappings": []})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 系统档案(.system 段)只读守卫:PUT/DELETE scope=system → 403
# ---------------------------------------------------------------------------

def _seed_system_profile(store):
    store.write(".system", [HostProfile(
        id="host_sys", name="futunn",
        source_url=None,
        mappings=[HostMapping(ip="10.0.0.1", host="api.futu.test")],
        scope="system",
    )])


def test_update_system_profile_forbidden(tmp_path):
    c, store = _client(tmp_path)
    _seed_system_profile(store)
    r = c.put("/api/workspaces/ws1/host-profiles/host_sys", json={
        "name": "futunn2", "mappings": []})
    assert r.status_code == 403
    # ws1 段未被污染
    assert store._read_segment("ws1") == []


def test_delete_system_profile_forbidden(tmp_path):
    c, store = _client(tmp_path)
    _seed_system_profile(store)
    r = c.delete("/api/workspaces/ws1/host-profiles/host_sys")
    assert r.status_code == 403
    # 系统档案仍在
    assert any(p.id == "host_sys" for p in store.read(".system"))


# ---------------------------------------------------------------------------
# fork:系统档案 → ws 可编辑副本(200 / 409 / 422 / 404)
# ---------------------------------------------------------------------------

def test_fork_endpoint_creates_ws_copy(tmp_path):
    c, store = _client(tmp_path)
    _seed_system_profile(store)
    r = c.post("/api/workspaces/ws1/host-profiles/host_sys/fork")
    assert r.status_code == 200, r.text
    forked = r.json()
    assert forked["id"] == "host_sys"
    assert forked["scope"] == "workspace"
    # ws 段持久化了副本
    assert any(p.id == "host_sys" for p in store._read_segment("ws1"))


def test_fork_endpoint_ws_profile_is_422(tmp_path):
    c, _store = _client(tmp_path)
    pid = c.post("/api/workspaces/ws1/host-profiles",
                 json={"name": "ws", "mappings": []}).json()["id"]
    r = c.post(f"/api/workspaces/ws1/host-profiles/{pid}/fork")
    assert r.status_code == 422


def test_fork_endpoint_duplicate_is_409(tmp_path):
    c, store = _client(tmp_path)
    _seed_system_profile(store)
    assert c.post("/api/workspaces/ws1/host-profiles/host_sys/fork").status_code == 200
    r = c.post("/api/workspaces/ws1/host-profiles/host_sys/fork")
    assert r.status_code == 409


def test_fork_endpoint_unknown_profile_is_404(tmp_path):
    c, _store = _client(tmp_path)
    r = c.post("/api/workspaces/ws1/host-profiles/host_nope/fork")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# parse:GET + 解析 /etc/hosts(不落盘 预览) + 失败 → 422
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parse_endpoint_no_persist(tmp_path, monkeypatch):
    """parse 返回 mappings + warnings,不落盘(list 仍空)。"""
    async def fake_fetch(url, timeout=15):
        return ([HostMapping(ip="10.0.0.1", host="x.test")], ["w1"])
    monkeypatch.setattr(
        "supernova_web.api.host_profiles.fetch_and_parse_hosts", fake_fetch)
    c, _store = _client(tmp_path)
    r = c.post("/api/workspaces/ws1/host-profiles/parse",
               params={"url": "https://h.test/get?id=1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(m["host"] == "x.test" for m in body["mappings"])
    assert body["warnings"] == ["w1"]
    # 不落盘
    assert c.get("/api/workspaces/ws1/host-profiles").json() == []


@pytest.mark.asyncio
async def test_parse_endpoint_failure_is_422(tmp_path, monkeypatch):
    """fetch_and_parse_hosts raise → 422。"""
    async def boom(url, timeout=15):
        raise OSError("net")
    monkeypatch.setattr(
        "supernova_web.api.host_profiles.fetch_and_parse_hosts", boom)
    c, _store = _client(tmp_path)
    r = c.post("/api/workspaces/ws1/host-profiles/parse",
               params={"url": "https://h.test/x"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# refresh:按 source_url 重新拉取并更新 mappings + 落盘
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_endpoint_updates_mappings(tmp_path, monkeypatch):
    """refresh:fetch_and_parse_hosts mock 返新 mappings → 落盘更新。

    monkeypatch api 层 fetch(由 store.refresh 调用)。
    """
    async def fake_fetch(url, timeout=15):
        return ([HostMapping(ip="10.0.0.1", host="updated.test")], [])
    monkeypatch.setattr(
        "supernova_web.components.host_profile_store.fetch_and_parse_hosts",
        fake_fetch)
    c, store = _client(tmp_path)
    pid = store.upsert_profile("ws1", HostProfile(
        id="host_x", name="x", source_url="https://h.test/get?id=1",
        mappings=[HostMapping(ip="10.0.0.1", host="old.test")],
        created_at="", updated_at="")).id
    r = c.post(f"/api/workspaces/ws1/host-profiles/{pid}/refresh")
    assert r.status_code == 200, r.text
    refreshed = r.json()
    assert any(m["host"] == "updated.test" for m in refreshed["mappings"])
    assert not any(m["host"] == "old.test" for m in refreshed["mappings"])


def test_refresh_unknown_returns_404(tmp_path):
    c, _store = _client(tmp_path)
    r = c.post("/api/workspaces/ws1/host-profiles/host_nope/refresh")
    assert r.status_code == 404
