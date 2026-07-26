import pytest
from pathlib import Path
from supernova_web.components.repo_manager import RepoManager
from supernova_web.components.git_fetcher import GitFetcher


@pytest.fixture
def rm(tmp_path):
    ws_dir = tmp_path / "workspaces"
    ws_dir.mkdir()
    (ws_dir / "ws1").mkdir()
    (ws_dir / "ws2").mkdir()
    # git_fetcher.available() 返 False 即可（不真 clone）；用 mock
    gf = GitFetcher(ws_dir, "u", "t")
    return RepoManager(ws_dir, gf)


def test_repos_root_per_workspace(rm, tmp_path):
    assert rm._repos_root("ws1") == tmp_path / "workspaces" / "ws1" / "repos"
    assert rm._repos_root("ws1") != rm._repos_root("ws2")


def test_list_repos_empty_per_ws(rm):
    assert rm.list_repos("ws1") == []
    assert rm.list_repos("ws2") == []


@pytest.mark.asyncio
async def test_clone_into_ws_dir(rm, monkeypatch):
    # mock git 子进程 + available
    monkeypatch.setattr(rm._git, "available", lambda: True)
    async def _fake_clone_task(self, *a, **kw):  # 不真跑 git
        self._jobs.pop(a[0] if isinstance(a[0], tuple) else (a[0]), None)
    monkeypatch.setattr(RepoManager, "_clone_task", _fake_clone_task)
    # _repo_dir(ws, name) 校验 + 建目录 + 写 meta（不真 clone）
    name = await rm.clone("ws1", "https://x/y.git", None, None, "y", None)
    assert (rm._workspaces_dir / "ws1" / "repos" / "y").exists()
    # ws2 不受影响
    assert not (rm._workspaces_dir / "ws2" / "repos" / "y").exists()


def test_repo_dir_isolation(rm):
    # ws1/repos/y 与 ws2/repos/y 是不同路径
    assert rm._repo_dir("ws1", "y") == rm._workspaces_dir / "ws1" / "repos" / "y"
    assert rm._repo_dir("ws2", "y") == rm._workspaces_dir / "ws2" / "repos" / "y"
