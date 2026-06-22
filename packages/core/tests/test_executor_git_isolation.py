"""隔离守门:executor.execute 必须先为 deliverables 建独立 git 仓库,
使 checkpoint/commit 落在独立仓库,不污染父(主)仓库。

这是本次修复的核心回归测试 —— 防止 ensure_repository 接入被移除后污染再现。
"""
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from shannon_core.agents.executor import AgentExecutor
from shannon_core.agents.runner import ClaudeRunResult
from shannon_core.git_manager import GitManager
from shannon_core.models.agents import AgentName


def _init_parent_repo(repo: Path) -> None:
    """把 repo 初始化成 git 仓库(模拟 shannon-py 主仓库)。"""
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "parent@local"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "parent"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "parent-initial"], cwd=repo, capture_output=True, check=True)


def _log_grep(repo: Path, pattern: str) -> str:
    return subprocess.run(
        ["git", "log", "--grep", pattern, "--oneline"],
        cwd=repo, capture_output=True, text=True,
    ).stdout


@pytest.mark.asyncio
async def test_execute_isolates_deliverables_from_parent_repo(tmp_path: Path, monkeypatch):
    """executor.execute 后:deliverables 有独立 .git;父仓库无 deliverable/checkpoint commit。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_parent_repo(repo)  # repo 模拟 shannon-py 主仓库
    deliverables = repo / "workspaces" / "session" / "deliverables"  # 嵌套在父仓库内

    # mock 重依赖,避免真 agent / 真校验;GitManager 用真的(测真 .git 隔离)
    async def fake_run(**kw):
        return ClaudeRunResult(text="ok", success=True, turns=1)
    monkeypatch.setattr("shannon_core.agents.executor.run_claude_prompt", fake_run)
    monkeypatch.setattr(
        "shannon_core.agents.executor.validate_deliverable", AsyncMock(),
    )

    prompt_manager = MagicMock()
    prompt_manager.load_sync.return_value = "prompt"
    executor = AgentExecutor(prompt_manager)

    await executor.execute(
        agent_name=AgentName.PRE_RECON,
        repo_path=str(repo),
        deliverables_path=str(deliverables),
    )

    # 1) deliverables 拥有独立 .git
    assert (deliverables / ".git").exists()
    # 2) deliverable/checkpoint commit 在 deliverables 独立仓库
    assert _log_grep(deliverables, "^deliverable:").strip() != ""
    # 3) 父仓库(repo)未被污染 —— 无 deliverable/checkpoint commit
    assert _log_grep(repo, "^deliverable:").strip() == ""
    assert _log_grep(repo, "^checkpoint:").strip() == ""
