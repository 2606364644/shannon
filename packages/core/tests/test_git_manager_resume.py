import asyncio
from pathlib import Path

import pytest

from supernova_core.git_manager import GitManager


def _init_repo(repo: Path) -> None:
    import subprocess
    for args in (["git", "init"], ["git", "config", "user.email", "t@t.com"],
                 ["git", "config", "user.name", "T"]):
        subprocess.run(args, cwd=repo, capture_output=True, check=True)
    (repo / "x.txt").write_text("init")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)


def _commit(repo: Path, msg: str) -> None:
    import subprocess
    (repo / "f.txt").write_text(msg)
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", msg],
                   cwd=repo, capture_output=True, check=True)


@pytest.mark.asyncio
async def test_get_completed_agents_parses_deliverable_commits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _commit(repo, "checkpoint: before pre-recon (attempt 1)")
    _commit(repo, "deliverable: pre-recon")
    _commit(repo, "checkpoint: before recon (attempt 1)")
    _commit(repo, "deliverable: recon")
    _commit(repo, "checkpoint: before injection-vuln (attempt 1)")  # 无 deliverable = 未完成

    result = await GitManager.get_completed_agents(repo)

    assert result == {"pre-recon", "recon"}


@pytest.mark.asyncio
async def test_get_completed_agents_empty_when_not_git_repo(tmp_path):
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()

    result = await GitManager.get_completed_agents(non_repo)

    assert result == set()
