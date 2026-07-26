"""Legacy repos API tests, ported to ws-scoped routes (P2: /api/workspaces/{ws}/repos/...).

T2 moved every repo route under the workspace context + added workspace_member authz.
These cases cover repo CRUD/SSE behavior (not membership) — admin role bypasses the
member check, so tester is created with role="admin" (see _authed). Membership-specific
cases live in test_repos_routes_ws.py.
"""
import json
import pytest
from fastapi.testclient import TestClient
from supernova_web.app import create_app


WS = "ws1"  # test workspace
BASE = f"/api/workspaces/{WS}/repos"


def _app(tmp_path, monkeypatch, repos):
    """repos: dict[repo_name -> state], laid out under workspaces/WS1/repos/.

    T1 made RepoManager per-ws (workspaces_dir/<ws>/repos); SUPERNOVA_REPOS_DIR no
    longer drives RepoManager. tmp_workspaces-style remount: SUPERNOVA_WORKER_ROOT
    = tmp_path so resolve_workspaces_dir() == tmp_path/"workspaces".
    """
    ws_root = tmp_path / "workspaces"
    ws_dir = ws_root / WS
    repos_dir = ws_dir / "repos"
    repos_dir.mkdir(parents=True, exist_ok=True)
    for name, state in repos.items():
        d = repos_dir / name; d.mkdir()
        (d / ".supernova-repo.json").write_text(json.dumps({"name": name, "state": state}))
        # 注：commit 628f2844（pre-T11）把 list_repos 顶层判定从 _is_repo 改为
        # (sub/.git).exists()；测试 setup 加 .git 模拟真 clone 产物（装配补全，非断言改动）。
        (d / ".git").mkdir()
    # T11 auth cascade: auth.db 落在 workspaces_dir/auth.db，必须先建好该目录，
    # 否则 AuthStore.init_schema() → sqlite3.OperationalError。remount 到 tmp_path
    # 让 workspaces_dir == ws_root（已 mkdir）。
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_web.config import get_config; get_config.cache_clear()
    return create_app()


def _authed(app):
    """构造已登录的 admin TestClient + 创建测试用户。返回 TestClient。

    tester 取 admin 角色——admin 经 workspace_member 依赖时 bypass 成员检查，
    使这些用例聚焦 repo CRUD/SSE 行为本身（成员鉴权另见 test_repos_routes_ws.py）。
    """
    from supernova_web.auth.passwords import hash_password
    store = app.state.auth_store
    if store.get_user_by_username("tester") is None:
        store.create_user("tester", hash_password("test-pw"), role="admin")
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": "tester", "password": "test-pw"},
           headers={"X-CSRF-Token": tok})
    return c


def _csrf(c):
    return c.get("/api/auth/csrf").json()["csrf_token"]


def test_list_repos(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"foo": "ready", "bar": "failed"})
    client = _authed(app)
    r = client.get(BASE)
    assert r.status_code == 200
    names = [x["name"] for x in r.json()]
    assert names == ["bar", "foo"]
    states = {x["name"]: x["state"] for x in r.json()}
    assert states["foo"] == "ready" and states["bar"] == "failed"


def test_get_repo_detail(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"foo": "ready"})
    client = _authed(app)
    r = client.get(f"{BASE}/foo")
    assert r.status_code == 200 and r.json()["name"] == "foo"
    assert client.get(f"{BASE}/missing").status_code == 404


def test_post_repo_503_no_creds(tmp_path, monkeypatch):
    monkeypatch.delenv("GITLAB_USER", raising=False)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    app = _app(tmp_path, monkeypatch, {})
    client = _authed(app)
    tok = _csrf(client)
    r = client.post(BASE, json={"git_url": "https://x/foo.git"}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 503


def test_post_repo_409_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("GITLAB_USER", "u"); monkeypatch.setenv("GITLAB_TOKEN", "t")
    app = _app(tmp_path, monkeypatch, {"foo": "ready"})
    client = _authed(app)
    tok = _csrf(client)
    r = client.post(BASE, json={"git_url": "https://x/foo.git"}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_delete_busy_409(tmp_path, monkeypatch):
    # 注：brief 原版为 sync + asyncio.create_task —— 3.12 无 running loop 会 RuntimeError。
    # 改 async（与 test_sse_events 同模式）即可让 asyncio.create_task 取得 loop。
    import asyncio
    import httpx
    app = _app(tmp_path, monkeypatch, {"foo": "cloning"})
    # T1: _jobs 键改为 (ws, name) 元组
    app.state.repo_manager._jobs[(WS, "foo")] = asyncio.create_task(asyncio.sleep(10))
    # 直接生成 session + csrf 注入 cookie（ASGITransport 不走 TestClient cookie jar）
    from supernova_web.auth.csrf import generate_csrf_token
    from supernova_web.auth.passwords import hash_password
    store = app.state.auth_store
    if store.get_user_by_username("tester") is None:
        store.create_user("tester", hash_password("test-pw"), role="admin")
    sid = app.state.session_manager.create(store.get_user_by_username("tester").id)
    tok = generate_csrf_token()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t",
                                 cookies={"sn-sid": sid, "sn-csrf": tok}) as c:
        r = await c.delete(f"{BASE}/foo", headers={"X-CSRF-Token": tok})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_sse_events(tmp_path, monkeypatch):
    import httpx
    app = _app(tmp_path, monkeypatch, {"foo": "ready"})
    (tmp_path / "workspaces" / WS / "repos" / "foo" / "clone.ndjson").write_text(
        json.dumps({"ts": "t1", "phase": "cloning", "progress": 40, "status": "progress"}) + "\n"
        + json.dumps({"ts": "t2", "type": "clone_end", "status": "ready"}) + "\n")
    # 直接生成 session 注入 cookie
    store = app.state.auth_store
    if store.get_user_by_username("tester") is None:
        from supernova_web.auth.passwords import hash_password
        store.create_user("tester", hash_password("test-pw"), role="admin")
    sid = app.state.session_manager.create(store.get_user_by_username("tester").id)
    transport = httpx.ASGITransport(app=app)
    lines = []
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=5,
                                 cookies={"sn-sid": sid}) as c:
        async with c.stream("GET", f"{BASE}/foo/events") as r:
            assert r.status_code == 200
            async for line in r.aiter_lines():
                lines.append(line)
                if line.startswith("data:") and "clone_end" in line:
                    break
    assert any("40" in l for l in lines if l.startswith("data:"))


def test_get_repo_grouped_path(tmp_path, monkeypatch):
    """{name:path} 吃 group/repo 含 '/' 的路径，返回 group 字段。"""
    app = _app(tmp_path, monkeypatch, {})
    client = _authed(app)
    base = tmp_path / "workspaces" / WS / "repos"
    d = base / "frontend" / "foo"; d.mkdir(parents=True)
    (d / ".supernova-repo.json").write_text(json.dumps({"name": "frontend/foo", "state": "ready"}))
    r = client.get(f"{BASE}/frontend/foo")
    assert r.status_code == 200
    assert r.json()["name"] == "frontend/foo"
    assert r.json()["group"] == "frontend"
    # 分组目录本身 404
    assert client.get(f"{BASE}/frontend").status_code == 404
