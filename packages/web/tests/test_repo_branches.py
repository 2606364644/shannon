"""分支枚举（list_branches / GET branches 端点）与 checkout 扫描锁测试。

spec：docs/superpowers/specs/2026-08-21-repo-branch-switch-design.md §2。
真 git 模式沿用 test_repo_manager.test_clone_real_git_succeeds（本地 bare origin，
file:// 免凭据绕开 _inject_auth）。
"""
import asyncio
import json
import subprocess

import pytest

WS = "ws1"
BASE = f"/api/workspaces/{WS}/repos"


def _origin_with_branches(tmp_path):
    """本地 bare origin，含 main + dev 两个已 push 的分支（供 ls-remote / checkout）。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   check=True, capture_output=True)
    work = tmp_path / "seedwork"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t.t"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"],
                   check=True, capture_output=True)
    (work / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "init"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "branch", "dev"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main", "dev"],
                   check=True, capture_output=True)
    return origin


def _clone_into(tmp_path, origin, name="foo", group=None):
    """手工 git clone 一个真仓进 ws repos（写 meta state=ready），供 ls-remote/checkout。"""
    base = tmp_path / "workspaces" / WS / "repos"
    target = base / group / name if group else base / name
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(target)],
                   check=True, capture_output=True)
    full = f"{group}/{name}" if group else name
    (target / ".supernova-repo.json").write_text(json.dumps({
        "name": full, "state": "ready",
        "source": {"kind": "git", "url": f"file://{origin}", "branch": "main"}}))
    return target


# ---- RepoManager.list_branches 层 ----

async def test_list_branches_parses_remote_heads(tmp_path, monkeypatch):
    """真 git：ls-remote --heads origin 列出 main+dev，去重排序；HEAD 行（无 tab refs/heads）不混入。"""
    origin = _origin_with_branches(tmp_path)
    _clone_into(tmp_path, origin)
    from tests.test_repo_manager import _rm
    rm = _rm(tmp_path, monkeypatch)
    branches = await rm.list_branches(WS, "foo")
    assert branches == ["dev", "main"]


async def test_list_branches_missing_repo_raises(tmp_path, monkeypatch):
    from tests.test_repo_manager import _rm
    rm = _rm(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="仓库不存在"):
        await rm.list_branches(WS, "ghost")


async def test_list_branches_busy_raises(tmp_path, monkeypatch):
    origin = _origin_with_branches(tmp_path)
    _clone_into(tmp_path, origin)
    from tests.test_repo_manager import _rm
    rm = _rm(tmp_path, monkeypatch)
    rm._jobs[(WS, "foo")] = asyncio.create_task(asyncio.sleep(10))
    try:
        with pytest.raises(ValueError, match="仓库正忙"):
            await rm.list_branches(WS, "foo")
    finally:
        rm._jobs.pop((WS, "foo"), None).cancel()


async def test_list_branches_ls_remote_failure_raises_runtimeerror(tmp_path, monkeypatch):
    """假 .git 目录（非真仓）→ git ls-remote rc!=0 → RuntimeError（端点映射 502）。"""
    base = tmp_path / "workspaces" / WS / "repos" / "foo"
    base.mkdir(parents=True)
    (base / ".git").mkdir()
    (base / ".supernova-repo.json").write_text(json.dumps({"name": "foo", "state": "ready"}))
    from tests.test_repo_manager import _rm
    rm = _rm(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="ls-remote"):
        await rm.list_branches(WS, "foo")


async def test_list_branches_timeout_raises_runtimeerror(tmp_path, monkeypatch):
    """wait_for 超时 → RuntimeError（与失败同走 502，不悬空协程）。"""
    base = tmp_path / "workspaces" / WS / "repos" / "foo"
    base.mkdir(parents=True)
    (base / ".git").mkdir()
    (base / ".supernova-repo.json").write_text(json.dumps({"name": "foo", "state": "ready"}))

    async def _timeout(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", _timeout)
    from tests.test_repo_manager import _rm
    rm = _rm(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="超时"):
        await rm.list_branches(WS, "foo")


# ---- GET /repos/{name}/branches 端点层 ----

def _app(tmp_path, monkeypatch, repos):
    from tests.test_api_repos import _app as make
    return make(tmp_path, monkeypatch, repos)


def _authed(app):
    from tests.test_api_repos import _authed as f
    return f(app)


def _csrf(c):
    return c.get("/api/auth/csrf").json()["csrf_token"]


def test_branches_endpoint_ok(tmp_path, monkeypatch):
    origin = _origin_with_branches(tmp_path)
    _clone_into(tmp_path, origin)
    app = _app(tmp_path, monkeypatch, {})
    client = _authed(app)
    r = client.get(f"{BASE}/foo/branches")
    assert r.status_code == 200, r.text
    assert r.json() == {"branches": ["dev", "main"]}


def test_branches_endpoint_grouped_path(tmp_path, monkeypatch):
    """group/repo（含 '/'）的 branches 不被贪婪 GET /{name:path} 吞（须声明在其之前）。"""
    origin = _origin_with_branches(tmp_path)
    _clone_into(tmp_path, origin, name="foo", group="frontend")
    app = _app(tmp_path, monkeypatch, {})
    client = _authed(app)
    r = client.get(f"{BASE}/frontend/foo/branches")
    assert r.status_code == 200, r.text
    assert r.json() == {"branches": ["dev", "main"]}


def test_branches_endpoint_404(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {})
    client = _authed(app)
    assert client.get(f"{BASE}/ghost/branches").status_code == 404


def test_branches_endpoint_405_linked(tmp_path, monkeypatch):
    from tests.test_api_repos import _ext_repo
    target = _ext_repo(tmp_path)
    app = _app(tmp_path, monkeypatch, {})
    app.state.repo_manager.link_repo(WS, "ftoa", str(target))
    client = _authed(app)
    assert client.get(f"{BASE}/ftoa/branches").status_code == 405


async def test_branches_endpoint_409_busy(tmp_path, monkeypatch):
    origin = _origin_with_branches(tmp_path)
    _clone_into(tmp_path, origin)
    app = _app(tmp_path, monkeypatch, {})
    app.state.repo_manager._jobs[(WS, "foo")] = asyncio.create_task(asyncio.sleep(10))
    try:
        # ASGITransport（同 delete_busy 模式）：TestClient sync 请求内 create_task 无 loop
        import httpx
        from supernova_web.auth.csrf import generate_csrf_token
        from supernova_web.auth.passwords import hash_password
        store = app.state.auth_store
        if store.get_user_by_username("admin") is None:
            store.create_user("admin", hash_password("test-pw"), role="admin")
        sid = app.state.session_manager.create(store.get_user_by_username("admin").id)
        tok = generate_csrf_token()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t",
                                     cookies={"sn-sid": sid, "sn-csrf": tok}) as c:
            r = await c.get(f"{BASE}/foo/branches", headers={"X-CSRF-Token": tok})
        assert r.status_code == 409
    finally:
        app.state.repo_manager._jobs.pop((WS, "foo"), None).cancel()


def test_branches_endpoint_502_ls_remote_failure(tmp_path, monkeypatch):
    """假 .git（非真仓）→ ls-remote 失败 → 502（前端降级手输），而非 500。"""
    app = _app(tmp_path, monkeypatch, {"foo": "ready"})
    client = _authed(app)
    r = client.get(f"{BASE}/foo/branches")
    assert r.status_code == 502, r.text


# ---- checkout 扫描引用锁（409）----

def test_checkout_scanning_409(tmp_path, monkeypatch):
    """仓库被在跑 scan 引用 → checkout 409（对齐 delete；worker 直读工作树，杜绝混合分支）。"""
    origin = _origin_with_branches(tmp_path)
    _clone_into(tmp_path, origin)
    app = _app(tmp_path, monkeypatch, {})
    monkeypatch.setattr(app.state.scan_manager, "active_repo_sources",
                        lambda: {(WS, "foo")})
    client = _authed(app)
    tok = _csrf(client)
    r = client.post(f"{BASE}/foo/checkout", json={"branch": "dev"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 409, r.text
    # 本地分支未被切走
    meta = json.loads(
        (tmp_path / "workspaces" / WS / "repos" / "foo" / ".supernova-repo.json").read_text())
    assert meta["source"]["branch"] == "main"


def test_checkout_not_scanning_switches(tmp_path, monkeypatch):
    """无 scan 引用 → 锁放行，真 git 切到 dev 并回写 meta（端到端）。"""
    origin = _origin_with_branches(tmp_path)
    target = _clone_into(tmp_path, origin)
    app = _app(tmp_path, monkeypatch, {})
    client = _authed(app)
    tok = _csrf(client)
    r = client.post(f"{BASE}/foo/checkout", json={"branch": "dev"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 200, r.text
    meta = json.loads((target / ".supernova-repo.json").read_text())
    assert meta["source"]["branch"] == "dev"
    assert meta["source"]["commit"]
    head = subprocess.run(["git", "-C", str(target), "rev-parse", "--abbrev-ref", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()
    assert head == "dev"
