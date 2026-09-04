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


def _mock_branch_exists(monkeypatch, *, exists=True, calls=None):
    """遮蔽 repos.branch_exists 接缝（外部 GitLab API，mock 不可避免）。"""
    async def fake_exists(link, branch, token):
        if calls is not None:
            calls.append((branch, token))
        return exists

    import supernova_web.api.repos as repos_mod
    monkeypatch.setattr(repos_mod, "branch_exists", fake_exists)


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


# ---- 端点：MR 已合并 + 源分支已删 → 自动改道 merge commit 增量（2026-09-04 shorturl !99 事故）----

def test_resolve_mr_merged_deleted_falls_back_to_merge_commit(tmp_path, monkeypatch):
    # GitLab 合并时勾「删源分支」是常态：不拦截，改道按 merge_commit_sha first-parent
    # 增量扫。head_ref 仍回填分支名（表单展示），实际扫描把手走 head_commit。
    app = _app(tmp_path, monkeypatch, {"repo": "ready"})
    client = _authed(app)
    _mock_mr(monkeypatch, result={"source_branch": "feature/safe",
                                  "target_branch": "master", "state": "merged",
                                  "merge_commit_sha": "6f77f8b2", "sha": "61da230a"})
    _mock_branch_exists(monkeypatch, exists=False)
    _mock_clone(app, monkeypatch, calls=[])
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["head_ref"] == "feature/safe"   # 展示仍用分支名
    assert body["base_ref"] == "master"
    assert body["mr_merged"] is True            # 改道标记（前端提示/提交透传用）
    assert body["head_commit"] == "6f77f8b2"    # true merge/squash：base 由 worker 算 ^1
    assert body["base_commit"] is None


def test_resolve_mr_merged_deleted_ff_uses_diff_refs(tmp_path, monkeypatch):
    # fast-forward 合并无 merge_commit_sha → 用 MR.sha + diff_refs.base_sha（两者都在
    # 目标分支历史上，可达可 fetch）。
    app = _app(tmp_path, monkeypatch, {"repo": "ready"})
    client = _authed(app)
    _mock_mr(monkeypatch, result={"source_branch": "feat/x",
                                  "target_branch": "main", "state": "merged",
                                  "merge_commit_sha": None, "sha": "abc1234",
                                  "diff_refs": {"base_sha": "10eb3bd",
                                                "head_sha": "abc1234"}})
    _mock_branch_exists(monkeypatch, exists=False)
    _mock_clone(app, monkeypatch, calls=[])
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mr_merged"] is True
    assert body["head_commit"] == "abc1234"
    assert body["base_commit"] == "10eb3bd"


def test_resolve_mr_merged_deleted_no_sha_handles_422(tmp_path, monkeypatch):
    # 连改道把手都没有（merge_commit_sha/sha/diff_refs 全缺）→ 无法定位合入内容，
    # 维持拦截并给引导（恢复分支 / 对目标分支全量扫）。
    app = _app(tmp_path, monkeypatch, {"repo": "ready"})
    client = _authed(app)
    _mock_mr(monkeypatch, result={"source_branch": "feature/safe",
                                  "target_branch": "master", "state": "merged",
                                  "merge_commit_sha": None, "sha": None,
                                  "diff_refs": None})
    _mock_branch_exists(monkeypatch, exists=False)
    clone_calls = []
    _mock_clone(app, monkeypatch, calls=clone_calls)
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "feature/safe" in detail and "已合并" in detail
    assert "恢复" in detail or "master" in detail  # 必须给出路
    assert clone_calls == []


def test_resolve_mr_closed_deleted_still_422(tmp_path, monkeypatch):
    # closed 未合并 + 分支已删：变更从未落地、commits 不可达 → 拦截（唯一出路是恢复分支）
    app = _app(tmp_path, monkeypatch, {"repo": "ready"})
    client = _authed(app)
    _mock_mr(monkeypatch, result={"source_branch": "feat/x",
                                  "target_branch": "main", "state": "closed",
                                  "merge_commit_sha": "6f77f8b2", "sha": "61da230a"})
    _mock_branch_exists(monkeypatch, exists=False)
    clone_calls = []
    _mock_clone(app, monkeypatch, calls=clone_calls)
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 422, r.text
    assert "已关闭" in r.json()["detail"]
    assert clone_calls == []


def test_resolve_mr_merged_deleted_clone_uses_base_ref(tmp_path, monkeypatch):
    # 首次贴链接（仓库未 clone）：源分支已删，按 head_ref clone 必失败 → 用目标分支
    app = _app(tmp_path, monkeypatch, {})
    client = _authed(app)
    _mock_mr(monkeypatch, result={"source_branch": "feature/safe",
                                  "target_branch": "master", "state": "merged",
                                  "merge_commit_sha": "6f77f8b2"})
    _mock_branch_exists(monkeypatch, exists=False)
    clone_calls = []
    _mock_clone(app, monkeypatch, calls=clone_calls)
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200, r.text
    assert len(clone_calls) == 1
    assert clone_calls[0]["branch"] == "master"  # 改道时 clone 目标分支（非已删源分支）


def test_resolve_mr_merged_branch_present_still_resolves(tmp_path, monkeypatch):
    # 合并时没删源分支的 MR 仍可增量扫描 → 正常回填不拦截
    app = _app(tmp_path, monkeypatch, {"repo": "ready"})
    client = _authed(app)
    _mock_mr(monkeypatch, result={"source_branch": "feat/x",
                                  "target_branch": "main", "state": "merged"})
    _mock_branch_exists(monkeypatch, exists=True)
    _mock_clone(app, monkeypatch, calls=[])
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200, r.text
    assert r.json()["head_ref"] == "feat/x"


def test_resolve_mr_opened_skips_branch_check(tmp_path, monkeypatch):
    # opened MR 源分支必然存在 → 不多打一次分支查询 API
    app = _app(tmp_path, monkeypatch, {"repo": "ready"})
    client = _authed(app)
    _mock_mr(monkeypatch, result={"source_branch": "feat/x",
                                  "target_branch": "main", "state": "opened"})
    branch_calls = []
    _mock_branch_exists(monkeypatch, exists=False, calls=branch_calls)
    _mock_clone(app, monkeypatch, calls=[])
    r = client.post(f"{BASE}/resolve-link", json={"url": MR_URL},
                    headers={"X-CSRF-Token": _csrf(client)})
    assert r.status_code == 200, r.text
    assert branch_calls == []


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


# ---- fetch_merge_request 字段映射 + merged 改道公式（mock httpx，外部 API 不可避免）----

class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """占位 httpx.AsyncClient：只实现 async-with + get，返回预置响应。"""

    resp = None

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        return self.resp


def test_fetch_merge_request_maps_commit_handles(monkeypatch):
    import asyncio
    from supernova_web.components import link_resolver as lr
    _FakeAsyncClient.resp = _FakeResp(payload={
        "source_branch": "feat/x", "target_branch": "main", "state": "merged",
        "merge_commit_sha": "6f77f8b2", "sha": "61da230a",
        "diff_refs": {"base_sha": "10eb3bd", "head_sha": "61da230a"}})
    monkeypatch.setattr(lr.httpx, "AsyncClient", _FakeAsyncClient)
    link = lr.classify_url(MR_URL)
    out = asyncio.run(lr.fetch_merge_request(link, "tok"))
    assert out["merge_commit_sha"] == "6f77f8b2"
    assert out["sha"] == "61da230a"
    assert out["diff_refs"]["base_sha"] == "10eb3bd"


def test_merged_fallback_commits_true_merge():
    # true merge/squash：merge_commit_sha 即把手，base 交给 worker 算 first-parent
    from supernova_web.components.link_resolver import merged_fallback_commits
    mr = {"merge_commit_sha": "6f77f8b2", "sha": "61da230a",
          "diff_refs": {"base_sha": "10eb3bd"}}
    assert merged_fallback_commits(mr) == ("6f77f8b2", None)


def test_merged_fallback_commits_ff():
    # fast-forward：无 merge_commit_sha → MR.sha + diff_refs.base_sha（都在目标分支历史上）
    from supernova_web.components.link_resolver import merged_fallback_commits
    mr = {"merge_commit_sha": None, "sha": "abc1234",
          "diff_refs": {"base_sha": "10eb3bd", "head_sha": "abc1234"}}
    assert merged_fallback_commits(mr) == ("abc1234", "10eb3bd")


def test_merged_fallback_commits_no_handles():
    # 全缺 → None（调用方维持拦截 422）
    from supernova_web.components.link_resolver import merged_fallback_commits
    assert merged_fallback_commits({"merge_commit_sha": None, "sha": None,
                                    "diff_refs": None}) is None
    assert merged_fallback_commits({}) is None
