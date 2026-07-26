"""P3c 阶段 4：RepoManager.clone 用 ws 凭据（_clone_task 把 ws 透传给 GitFetcher）。

spy _inject_auth / available 捕获 ws；mock 底层 _run_git_with_progress / _head_commit
（不真跑 git clone），让真实 _clone_task 跑通以验证 ws 透传。
"""
from unittest.mock import AsyncMock

import pytest
from supernova_web.components.git_fetcher import GitFetcher
from supernova_web.components.repo_manager import RepoManager


@pytest.fixture
def rm(tmp_path):
    ws_dir = tmp_path / "workspaces"
    ws_dir.mkdir()
    (ws_dir / "ws-a").mkdir()
    gf = GitFetcher(ws_dir, "global-user", "global-token")
    return RepoManager(ws_dir, gf)


@pytest.mark.asyncio
async def test_clone_passes_ws_to_git_fetcher(rm, monkeypatch):
    """clone(ws=...) → _clone_task 把 ws 透传给 _git.available(ws)/_inject_auth(url, ws)。"""
    avail_calls = []

    def avail_spy(ws=None):
        avail_calls.append(ws)
        return True

    monkeypatch.setattr(rm._git, "available", avail_spy)

    inject_calls = []
    real_inject = rm._git._inject_auth

    def inject_spy(url, ws=None):
        inject_calls.append((url, ws))
        return real_inject(url, ws)

    monkeypatch.setattr(rm._git, "_inject_auth", inject_spy)

    # mock 底层 git 执行（不真 clone），让真实 _clone_task 跑通
    monkeypatch.setattr(rm, "_run_git_with_progress", AsyncMock(return_value=True))
    monkeypatch.setattr(rm, "_head_commit", AsyncMock(return_value="deadbeef"))

    name = await rm.clone("ws-a", "https://x/y.git", None, None, "y", None)
    # clone() 用 asyncio.create_task 启动 _clone_task；等它完成
    task = rm._jobs.get(("ws-a", name))
    if task is not None:
        await task

    assert "ws-a" in avail_calls, f"available 应收到 ws-a，实际 {avail_calls}"
    assert ("https://x/y.git", "ws-a") in inject_calls, (
        f"_inject_auth 应收到 (url, ws-a)，实际 {inject_calls}"
    )
