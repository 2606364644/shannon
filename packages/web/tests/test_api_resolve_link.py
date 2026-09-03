"""统一链接解析端点 POST /api/workspaces/{ws}/resolve-link（扫描发起页仓库入口整合 A 段）。

三类输入：GitLab MR 链接（调 GitLab API 回填 base/head refs）/ 仓库链接（直接匹配，
不调 API）/ 不识别（422）。仓库不在工作区 → 触发 rm.clone 异步下载（端点不等待）→
repo_state="cloning"。仓库匹配两级探测：完整 project path（group/repo）优先，回落
扁平名（repo_name 语义——clone 默认落扁平名）。

GitLab API 经 monkeypatch supernova_web.api.repos.fetch_merge_request 接缝注入
（外部服务，mock 不可避免）；clone 经实例级 monkeypatch rm.clone 记录副作用。
"""
import json

import pytest

from supernova_web.app import create_app

WS = "ws1"
BASE = f"/api/workspaces/{WS}"

MR_URL = "https://git.example.com/group/repo/-/merge_requests/42"
REPO_URL = "https://git.example.com/group/repo"


# ---- 装配（对齐 test_api_repos._app/_authed/_csrf 模式）----

def _app(tmp_path, monkeypatch, repos, creds=True):
    # GITLAB_* 必须在 create_app 前 setenv——GitFetcher 构造时从 env 快照全局凭据，
    # 构造后再设不生效（creds=False 用例模拟"未配置凭据"）。
    if creds:
        monkeypatch.setenv("GITLAB_USER", "u")
        monkeypatch.setenv("GITLAB_TOKEN", "t")
    else:
        monkeypatch.delenv("GITLAB_USER", raising=False)
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    repos_dir = tmp_path / "workspaces" / WS / "repos"
    repos_dir.mkdir(parents=True, exist_ok=True)
    for name, state in repos.items():
        d = repos_dir / name
        d.mkdir(parents=True)
        (d / ".supernova-repo.json").write_text(json.dumps({"name": name, "state": state}))
        (d / ".git").mkdir()  # 模拟真 clone 产物（list/get 判定需要）
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_web.config import get_config
    get_config.cache_clear()
    return create_app()


def _authed(app):
    from fastapi.testclient import TestClient
    from supernova_web.auth.passwords import hash_password
    store = app.state.auth_store
    if store.get_user_by_username("admin") is None:
        store.create_user("admin", hash_password("test-pw"), role="admin")
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": "admin", "password": "test-pw"},
           headers={"X-CSRF-Token": tok})
    return c


def _csrf(c):
    return c.get("/api/auth/csrf").json()["csrf_token"]


def _mock_mr(monkeypatch, *, result=None, error=None, calls=None):
    async def fake_fetch(link, token):
        if calls is not None:
            calls.append((link, token))
        if error is not None:
            raise error
        return result or {}

    import supernova_web.api.repos as repos_mod
    monkeypatch.setattr(repos_mod, "fetch_merge_request", fake_fetch)


def _mock_clone(app, monkeypatch, calls=None, error=None):
    """实例级遮蔽 rm.clone：记录调用参数，不起真 git 子进程。"""
    async def fake_clone(ws, url, branch, commit, name, group=None):
        if calls is not None:
            calls.append({"ws": ws, "url": url, "branch": branch,
                          "commit": commit, "name": name, "group": group})
        if error is not None:
            raise error
        return name or "repo"

    monkeypatch.setattr(app.state.repo_manager, "clone", fake_clone)


# ---- classify_url 纯函数 ----

def test_classify_mr_link():
    from supernova_web.components.link_resolver import MrLink, classify_url
    link = classify_url(MR_URL)
    assert isinstance(link, MrLink)
    assert link.scheme == "https" and link.host == "git.example.com"
    assert link.project == "group/repo" and link.iid == 42


def test_classify_mr_link_tolerates_query_and_trailing_slash():
    from supernova_web.components.link_resolver import MrLink, classify_url
    link = classify_url(MR_URL + "/?diff_id=3")
    assert isinstance(link, MrLink)
    assert link.project == "group/repo" and link.iid == 42


def test_classify_repo_link_strips_git_suffix():
    from supernova_web.components.link_resolver import RepoLink, classify_url
    link = classify_url(REPO_URL + ".git")
    assert isinstance(link, RepoLink)
    assert link.project == "group/repo" and link.host == "git.example.com"


def test_classify_nested_group():
    from supernova_web.components.link_resolver import RepoLink, classify_url
    link = classify_url("https://git.example.com/a/b/repo")
    assert isinstance(link, RepoLink)
    assert link.project == "a/b/repo"


def test_classify_github_pr_unsupported():
    from supernova_web.components.link_resolver import UnsupportedLinkError, classify_url
    with pytest.raises(UnsupportedLinkError):
        classify_url("https://github.com/foo/bar/pull/1")


def test_classify_bad_url():
    from supernova_web.components.link_resolver import UnsupportedLinkError, classify_url
    with pytest.raises(UnsupportedLinkError):
        classify_url("not a url")
    with pytest.raises(UnsupportedLinkError):
        classify_url("https://host")  # 无 project path
    with pytest.raises(UnsupportedLinkError):
        classify_url("")


# ---- 端点：MR 链接 ----

def test_resolve_mr_repo_ready(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"group/repo": "ready"})
    client = _authed(app)
    _mock_mr(monkeypatch, result={"source_branch": "feat/x", "target_branch": "main"})
    clone_calls = []
    _mock_clone(app, monkeypatch, calls=clone_calls)
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200, r.text
    assert r.json() == {"kind": "mr", "repo": "group/repo",
                        "base_ref": "main", "head_ref": "feat/x",
                        "repo_state": "ready"}
    assert clone_calls == []  # 已存在不 clone


def test_resolve_mr_flat_name_match(tmp_path, monkeypatch):
    # clone 默认落扁平名：MR 链接 project=group/repo 也应匹配到扁平 repo
    app = _app(tmp_path, monkeypatch, {"repo": "ready"})
    client = _authed(app)
    _mock_mr(monkeypatch, result={"source_branch": "feat/x", "target_branch": "main"})
    _mock_clone(app, monkeypatch, calls=[])
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200
    assert r.json()["repo"] == "repo" and r.json()["repo_state"] == "ready"


def test_resolve_mr_triggers_clone_when_missing(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {})
    client = _authed(app)
    _mock_mr(monkeypatch, result={"source_branch": "feat/x", "target_branch": "main"})
    clone_calls = []
    _mock_clone(app, monkeypatch, calls=clone_calls)
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "mr" and body["repo_state"] == "cloning"
    assert body["base_ref"] == "main" and body["head_ref"] == "feat/x"
    assert len(clone_calls) == 1
    call = clone_calls[0]
    assert call["url"] == "https://git.example.com/group/repo"
    assert call["branch"] == "feat/x"  # clone 后 checkout 到 MR 源分支


def test_resolve_mr_prefers_full_path_over_flat(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"group/repo": "ready", "repo": "ready"})
    client = _authed(app)
    _mock_mr(monkeypatch, result={"source_branch": "f", "target_branch": "m"})
    _mock_clone(app, monkeypatch, calls=[])
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200
    assert r.json()["repo"] == "group/repo"


def test_resolve_mr_api_404(tmp_path, monkeypatch):
    from supernova_web.components.link_resolver import GitLabApiError
    app = _app(tmp_path, monkeypatch, {"repo": "ready"})
    client = _authed(app)
    _mock_mr(monkeypatch, error=GitLabApiError(404, "Not Found"))
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 404


def test_resolve_mr_api_401(tmp_path, monkeypatch):
    from supernova_web.components.link_resolver import GitLabApiError
    app = _app(tmp_path, monkeypatch, {})
    client = _authed(app)
    monkeypatch.setenv("GITLAB_USER", "u")
    monkeypatch.setenv("GITLAB_TOKEN", "bad")
    _mock_mr(monkeypatch, error=GitLabApiError(401, "Unauthorized"))
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 502  # 上游凭据拒绝（对齐 branches 端点的 502 语义）


def test_resolve_mr_no_creds_503(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"repo": "ready"}, creds=False)
    client = _authed(app)
    calls = []
    _mock_mr(monkeypatch, calls=calls)
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 503
    assert calls == []  # 凭据缺失先于 API 调用拒绝


# ---- 端点：仓库链接 ----

def test_resolve_repo_url_ready(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"repo": "ready"})
    client = _authed(app)
    clone_calls = []
    _mock_clone(app, monkeypatch, calls=clone_calls)
    r = client.post(f"{BASE}/resolve-link", json={"url": REPO_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200, r.text
    assert r.json() == {"kind": "repo", "repo": "repo", "repo_state": "ready"}
    assert clone_calls == []


def test_resolve_repo_url_clone(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {})
    client = _authed(app)
    clone_calls = []
    _mock_clone(app, monkeypatch, calls=clone_calls)
    r = client.post(f"{BASE}/resolve-link", json={"url": REPO_URL + ".git"},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"kind": "repo", "repo": "repo", "repo_state": "cloning"}
    assert len(clone_calls) == 1
    assert clone_calls[0]["url"] == REPO_URL + ".git"
    assert clone_calls[0]["branch"] is None  # 仓库链接不指定分支（默认分支）


def test_resolve_repo_url_clone_no_creds_503(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {}, creds=False)
    client = _authed(app)
    _mock_clone(app, monkeypatch, error=PermissionError("未配置 git 凭证（GITLAB_USER/TOKEN）"))
    r = client.post(f"{BASE}/resolve-link", json={"url": REPO_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 503


def test_resolve_repo_url_clone_conflict_409(tmp_path, monkeypatch):
    from supernova_web.components.repo_manager import TooManyClones
    app = _app(tmp_path, monkeypatch, {})
    client = _authed(app)
    _mock_clone(app, monkeypatch, error=TooManyClones(3))
    r = client.post(f"{BASE}/resolve-link", json={"url": REPO_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 409


# ---- 端点：非法输入与鉴权 ----

def test_resolve_github_pr_422(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {})
    client = _authed(app)
    r = client.post(f"{BASE}/resolve-link",
                    json={"url": "https://github.com/foo/bar/pull/1"},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 422


def test_resolve_garbage_422(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {})
    client = _authed(app)
    r = client.post(f"{BASE}/resolve-link", json={"url": "not a url"},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 422


def test_resolve_requires_auth(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    app = _app(tmp_path, monkeypatch, {})
    client = TestClient(app)  # 未登录
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL})
    assert r.status_code in (401, 403)
