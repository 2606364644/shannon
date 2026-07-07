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
