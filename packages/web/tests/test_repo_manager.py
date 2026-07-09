import asyncio
import json
import sys
import textwrap
import pytest
from shannon_web.components.repo_manager import RepoManager, TooManyClones


def _rm(tmp_path, monkeypatch) -> RepoManager:
    monkeypatch.setenv("SHANNON_REPOS_DIR", str(tmp_path / "repos"))
    from shannon_web.config import get_config; get_config.cache_clear()
    from shannon_web.components.git_fetcher import GitFetcher
    cfg = get_config()
    return RepoManager(cfg.repos_dir, GitFetcher(cfg.repos_dir, "u", "t"), max_concurrent=3)


@pytest.fixture
def fake_clone_ok(tmp_path):
    """假 git 子进程：写一行 progress 到 stderr 后退出 0。"""
    s = tmp_path / "ok.py"
    s.write_text(textwrap.dedent('''
        import sys
        sys.stderr.write("Receiving objects: 40%\\n")
        sys.exit(0)
    '''))
    return s


@pytest.mark.asyncio
async def test_clone_writes_ndjson_and_meta(tmp_path, monkeypatch, fake_clone_ok):
    rm = _rm(tmp_path, monkeypatch)
    monkeypatch.setattr(rm, "_build_clone_argv",
                        lambda url, target, branch: [sys.executable, str(fake_clone_ok)])
    name = await rm.clone("https://gitlab.example/foo.git", None, None, None)
    assert name == "foo"
    # 等 task 结束
    await asyncio.sleep(0.3)
    meta = json.loads((tmp_path / "repos" / "foo" / ".shannon-repo.json").read_text())
    assert meta["state"] == "ready"
    lines = (tmp_path / "repos" / "foo" / "clone.ndjson").read_text().splitlines()
    assert any("40" in l and '"progress"' in l for l in lines)
    assert any('"clone_end"' in l and '"ready"' in l for l in lines)


@pytest.mark.asyncio
async def test_clone_failed_writes_failed_state(tmp_path, monkeypatch):
    crash = tmp_path / "crash.py"
    crash.write_text('import sys; sys.stderr.write("boom https://u:t@host\\n"); sys.exit(1)')
    rm = _rm(tmp_path, monkeypatch)
    monkeypatch.setattr(rm, "_build_clone_argv",
                        lambda url, target, branch: [sys.executable, str(crash)])
    await rm.clone("https://gitlab.example/foo.git", None, None, None)
    await asyncio.sleep(0.3)
    meta = json.loads((tmp_path / "repos" / "foo" / ".shannon-repo.json").read_text())
    assert meta["state"] == "failed"
    assert "t@" not in meta["last_error"]  # token 脱敏（last_error 是固定串，本就行）
    # 真正的安全属性：stderr 的 `https://u:t@host` 经 _git.redact 写进 clone.ndjson 的
    # message 字段，必须脱敏为 `https://***:***@host`，绝不含 `u:t@` token 模式。
    ndjson = (tmp_path / "repos" / "foo" / "clone.ndjson").read_text("utf-8", errors="replace")
    assert "u:t@" not in ndjson
    assert "***:***@" in ndjson


@pytest.mark.asyncio
async def test_clone_rejects_path_traversal_name(tmp_path, monkeypatch):
    """name 含路径分隔符/遍历分量必须 ValueError，防止越界 repos_dir mkdir/clone/rmtree
    （final-review I-6）。

    注：name=None 走 repo_name(url) 派生路径（合法，不进 _validate_repo_name），
    故只测显式传非法名的情形。
    """
    rm = _rm(tmp_path, monkeypatch)
    # 防御：若校验被绕过，绝不能真起 git clone 子进程（会挂）
    def _no_real_clone(*a, **kw):
        raise AssertionError("validation should block before _build_clone_argv")
    monkeypatch.setattr(rm, "_build_clone_argv", _no_real_clone)
    for evil in ("../evil", "a/b", ".", "..", "x\\y", "x/y"):
        with pytest.raises(ValueError, match="非法仓库名"):
            await rm.clone("https://gitlab.example/foo.git", None, None, evil)


@pytest.mark.asyncio
async def test_name_conflict_raises(tmp_path, monkeypatch):
    rm = _rm(tmp_path, monkeypatch)
    (tmp_path / "repos").mkdir()
    (tmp_path / "repos" / "foo").mkdir()
    with pytest.raises(ValueError, match="已存在"):
        await rm.clone("https://gitlab.example/foo.git", None, None, None)


@pytest.mark.asyncio
async def test_concurrent_limit(tmp_path, monkeypatch):
    rm = _rm(tmp_path, monkeypatch)
    # Plan-internal deviation: brief's clone() guard is `len(self._jobs) >= self._max_concurrent`.
    # _rm hardcodes max_concurrent=3, so override to 1 to make len(_jobs)=1 trigger the raise.
    rm._max_concurrent = 1
    rm._sem = asyncio.Semaphore(1)  # secondary backstop (harmless)
    rm._jobs["busy"] = asyncio.create_task(asyncio.sleep(10))
    with pytest.raises(TooManyClones):
        await rm.clone("https://gitlab.example/bar.git", None, None, None)


@pytest.mark.asyncio
async def test_clone_with_group(tmp_path, monkeypatch, fake_clone_ok):
    """clone(group=...) 落到 repos/<group>/<name>，返回 group/repo 名。"""
    rm = _rm(tmp_path, monkeypatch)
    monkeypatch.setattr(rm, "_build_clone_argv",
                        lambda url, target, branch: [sys.executable, str(fake_clone_ok)])
    name = await rm.clone("https://gitlab.example/foo.git", None, None, None, group="frontend")
    assert name == "frontend/foo"
    await asyncio.sleep(0.3)
    meta_path = tmp_path / "repos" / "frontend" / "foo" / ".shannon-repo.json"
    assert meta_path.exists()
    assert json.loads(meta_path.read_text())["state"] == "ready"


def test_list_repos_groups(tmp_path, monkeypatch):
    """分组目录下的真仓库识别为 group/repo；分组目录本身不当仓库；同名跨组不冲突。"""
    rm = _rm(tmp_path, monkeypatch)
    base = tmp_path / "repos"
    for rel in ["frontend/foo", "backend/honor", "frontend/honor"]:
        d = base / rel; d.mkdir(parents=True)
        (d / ".git").mkdir()  # 真仓库标志
    (base / "baz").mkdir()
    (base / "baz" / ".git").mkdir()  # 扁平仓库
    views = {r["name"]: r for r in rm.list_repos()}
    assert set(views) == {"frontend/foo", "backend/honor", "frontend/honor", "baz"}
    assert "frontend" not in views and "backend" not in views  # 分组目录不入列
    assert views["frontend/foo"]["group"] == "frontend"
    assert views["baz"]["group"] is None
    # frontend/honor 与 backend/honor 同名跨组共存
    assert "frontend/honor" in views and "backend/honor" in views


def test_get_repo_rejects_group_dir(tmp_path, monkeypatch):
    """get_repo 对分组目录（无 .git 无 meta）返回 None，不当作仓库。"""
    rm = _rm(tmp_path, monkeypatch)
    base = tmp_path / "repos"
    (base / "frontend" / "foo").mkdir(parents=True)
    (base / "frontend" / "foo" / ".git").mkdir()
    assert rm.get_repo("frontend/foo") is not None
    assert rm.get_repo("frontend") is None  # 分组目录


@pytest.mark.asyncio
async def test_delete_does_not_rmtree_group_dir(tmp_path, monkeypatch):
    """delete 分组目录绝不能 rmtree 子仓库（误删/越界防护）。"""
    rm = _rm(tmp_path, monkeypatch)
    base = tmp_path / "repos"
    for rel in ["frontend/foo", "frontend/bar"]:
        d = base / rel; d.mkdir(parents=True)
        (d / ".git").mkdir()
    await rm.delete("frontend")  # 分组目录：不应删任何子仓库
    assert (base / "frontend" / "foo" / ".git").exists()
    assert (base / "frontend" / "bar" / ".git").exists()


def test_validate_repo_name_allows_group():
    """_validate_repo_name 允许 group/repo，拒多层/遍历/空段/首尾斜杠。"""
    from shannon_web.components.repo_manager import _validate_repo_name
    _validate_repo_name("foo")
    _validate_repo_name("frontend/foo")
    for evil in ("a/b/c", "../evil", "a//b", "/a", "a/", "a/../b", ".", "..", "a\\b"):
        with pytest.raises(ValueError, match="非法"):
            _validate_repo_name(evil)


def test_list_repos_treats_group_dir_with_stale_meta_as_group(tmp_path, monkeypatch):
    """分组目录有残留 .shannon-repo.json（旧版误写）时，list_repos 仍当分组深入一层，
    不把分组目录本身当仓库（回归 2026-07-08 /repos 把分组目录当仓库、真仓库全被吞的 bug）。"""
    rm = _rm(tmp_path, monkeypatch)
    base = tmp_path / "repos"
    (base / "frontend" / "foo").mkdir(parents=True)
    (base / "frontend" / "foo" / ".git").mkdir()  # 子仓库
    # 旧版误写到分组目录的脏 meta（kind=unknown，非 clone 产物）
    (base / "frontend" / ".shannon-repo.json").write_text(
        json.dumps({"name": "frontend", "source": {"kind": "unknown"}, "state": "ready"}))
    views = {r["name"]: r for r in rm.list_repos()}
    assert set(views) == {"frontend/foo"}
    assert "frontend" not in views  # 分组目录（即使有脏 meta）不入列
    assert views["frontend/foo"]["group"] == "frontend"


def test_migrate_legacy_cleans_miswritten_group_meta(tmp_path, monkeypatch):
    """migrate_legacy 清理被旧版误写到分组目录的脏 meta（有 meta、无 .git、且含子仓库）。"""
    rm = _rm(tmp_path, monkeypatch)
    base = tmp_path / "repos"
    (base / "backend" / "honor").mkdir(parents=True)
    (base / "backend" / "honor" / ".git").mkdir()  # 子仓库
    stale = base / "backend" / ".shannon-repo.json"
    stale.write_text(json.dumps({"name": "backend", "source": {"kind": "unknown"}, "state": "ready"}))
    rm.migrate_legacy()
    assert not stale.exists()  # 脏 meta 被清
    # 子仓库被正常 migrate（补 meta）
    assert (base / "backend" / "honor" / ".shannon-repo.json").exists()


# ---- source.url 凭据脱敏（GitLab token 泄露安全修复）----
# 泄露路径：带凭据的 clone URL（https://oauth2:TOKEN@host 或 _inject_auth 注入的
# USER:TOKEN@）会原样落盘 .shannon-repo.json 的 source.url，并经 _repo_view 透传给
# /api/repos 返回前端。这些测试锁定：落盘前 + 出站前都必须剥离 userinfo。

def test_strip_credentials():
    """strip_credentials 剥离 URL userinfo（username/password），保留 host/port/path/query。"""
    from shannon_web.components.git_fetcher import strip_credentials
    # 标准带凭据 https
    assert strip_credentials("https://oauth2:glpat-xyz@gitlab.example/foo.git") == \
        "https://gitlab.example/foo.git"
    # 带 port + query + fragment
    assert strip_credentials("https://u:p@host:8080/a/b?x=1#frag") == "https://host:8080/a/b?x=1#frag"
    # 仅 username 无 password
    assert strip_credentials("https://u@host/p") == "https://host/p"
    # 无凭据原样返回（不重建、不改动）
    assert strip_credentials("https://gitlab.example/foo.git") == "https://gitlab.example/foo.git"
    # 非 http(s) 不动（SSH git@host:path 等）
    assert strip_credentials("git@gitlab.example:foo.git") == "git@gitlab.example:foo.git"
    # 空值
    assert strip_credentials(None) is None
    assert strip_credentials("") == ""


@pytest.mark.asyncio
async def test_clone_strips_credentials_from_source_url(tmp_path, monkeypatch, fake_clone_ok):
    """clone 带凭据的 URL：落盘 meta 与 get_repo 出站的 source.url 都须剥离 token。"""
    rm = _rm(tmp_path, monkeypatch)
    monkeypatch.setattr(rm, "_build_clone_argv",
                        lambda url, target, branch: [sys.executable, str(fake_clone_ok)])
    secret = "https://oauth2:glpat-LEAK-TOKEN@gitlab.example/foo.git"
    await rm.clone(secret, None, None, None)
    await asyncio.sleep(0.3)
    # 落盘 meta 不含 token
    meta = json.loads((tmp_path / "repos" / "foo" / ".shannon-repo.json").read_text())
    assert "glpat-LEAK-TOKEN" not in json.dumps(meta)
    assert meta["source"]["url"] == "https://gitlab.example/foo.git"
    # 出站（get_repo）也不含 token
    view = rm.get_repo("foo")
    assert "glpat-LEAK-TOKEN" not in json.dumps(view)
    assert view["source"]["url"] == "https://gitlab.example/foo.git"


def test_get_repo_redacts_credentials_in_legacy_meta(tmp_path, monkeypatch):
    """旧 meta 已含带凭据的 source.url（历史/手动写入）：get_repo 出站兜底仍须剥离。"""
    rm = _rm(tmp_path, monkeypatch)
    base = tmp_path / "repos"
    d = base / "foo"
    d.mkdir(parents=True)
    (d / ".git").mkdir()
    (d / ".shannon-repo.json").write_text(json.dumps({
        "name": "foo",
        "source": {"kind": "git", "url": "https://oauth2:glpat-LEGACY-LEAK@gitlab.example/foo.git"},
        "state": "ready",
    }))
    view = rm.get_repo("foo")
    assert "glpat-LEGACY-LEAK" not in json.dumps(view)
    assert view["source"]["url"] == "https://gitlab.example/foo.git"


def test_list_repos_redacts_credentials(tmp_path, monkeypatch):
    """list_repos 出站清洗带凭据的 source.url（_repo_view 是共同出口）。"""
    rm = _rm(tmp_path, monkeypatch)
    base = tmp_path / "repos"
    d = base / "foo"
    d.mkdir(parents=True)
    (d / ".git").mkdir()
    (d / ".shannon-repo.json").write_text(json.dumps({
        "name": "foo",
        "source": {"kind": "git", "url": "https://oauth2:glpat-LIST-LEAK@gitlab.example/foo.git"},
        "state": "ready",
    }))
    blob = json.dumps(rm.list_repos())
    assert "glpat-LIST-LEAK" not in blob


def test_migrate_strips_credentials_from_git_config(tmp_path, monkeypatch):
    """migrate_legacy 从 .git/config 读回带凭据 url（_inject_auth 注入污染）：落盘须剥离。"""
    rm = _rm(tmp_path, monkeypatch)
    base = tmp_path / "repos"
    d = base / "foo"
    d.mkdir(parents=True)
    gitcfg = d / ".git"
    gitcfg.mkdir()
    # _inject_auth 注入后 git 写入 config 的带凭据 url
    (gitcfg / "config").write_text(
        '[remote "origin"]\n\turl = https://u:glpat-MIGRATE-LEAK@gitlab.example/foo.git\n')
    (gitcfg / "HEAD").write_text("ref: refs/heads/main\n")
    rm.migrate_legacy()
    meta = json.loads((d / ".shannon-repo.json").read_text())
    assert "glpat-MIGRATE-LEAK" not in json.dumps(meta)
    assert meta["source"]["url"] == "https://gitlab.example/foo.git"
