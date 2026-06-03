import asyncio
import subprocess

import pytest
from pathlib import Path

from shannon_core.git_manager import GitManager
from shannon_core.models.errors import PentestError
from shannon_core.models.result import GitResult


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repository with one initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, capture_output=True, check=True,
    )
    (repo / "initial.txt").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo, capture_output=True, check=True,
    )
    return repo


# ---- is_git_repository ----


async def test_is_git_repository_true(git_repo: Path):
    assert await GitManager.is_git_repository(git_repo) is True


async def test_is_git_repository_false(tmp_path: Path):
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()
    assert await GitManager.is_git_repository(non_repo) is False


# ---- get_commit_hash ----


async def test_get_commit_hash(git_repo: Path):
    h = await GitManager.get_commit_hash(git_repo)
    assert h is not None
    assert len(h) == 40


async def test_get_commit_hash_non_repo(tmp_path: Path):
    h = await GitManager.get_commit_hash(tmp_path / "nonexistent")
    assert h is None


# ---- create_checkpoint ----


async def test_create_checkpoint_attempt1(git_repo: Path):
    result = await GitManager.create_checkpoint(git_repo, "pre-recon", attempt=1)
    assert result.success is True


async def test_create_checkpoint_attempt1_preserves_files(git_repo: Path):
    """On first attempt, existing uncommitted files should be preserved in the checkpoint."""
    (git_repo / "existing.txt").write_text("kept")
    await GitManager.create_checkpoint(git_repo, "pre-recon", attempt=1)
    assert (git_repo / "existing.txt").exists()
    # The file should be committed in the checkpoint
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=git_repo, capture_output=True, text=True, check=True,
    )
    assert "checkpoint" in log.stdout


async def test_create_checkpoint_retry_cleans_first(git_repo: Path):
    """On attempt > 1, workspace should be cleaned before checkpoint."""
    (git_repo / "stale.txt").write_text("stale")
    subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True, check=True)

    result = await GitManager.create_checkpoint(git_repo, "pre-recon", attempt=2)
    assert result.success is True
    assert not (git_repo / "stale.txt").exists()


async def test_create_checkpoint_non_git_dir(tmp_path: Path):
    non_repo = tmp_path / "not-git"
    non_repo.mkdir()
    result = await GitManager.create_checkpoint(non_repo, "agent")
    assert result.success is True
    assert result.changed_files == []


# ---- commit ----


async def test_commit_success(git_repo: Path):
    (git_repo / "deliverable.md").write_text("# Report")
    result = await GitManager.commit(git_repo, "pre-recon")
    assert result.success is True
    assert len(result.changed_files) > 0
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=git_repo, capture_output=True, text=True, check=True,
    )
    assert "deliverable: pre-recon" in log.stdout


async def test_commit_non_git_dir(tmp_path: Path):
    non_repo = tmp_path / "not-git"
    non_repo.mkdir()
    result = await GitManager.commit(non_repo, "agent")
    assert result.success is True


# ---- rollback ----


async def test_rollback_removes_files(git_repo: Path):
    (git_repo / "bad_file.txt").write_text("bad content")
    subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True, check=True)
    result = await GitManager.rollback(git_repo, "test failure")
    assert result.success is True
    assert not (git_repo / "bad_file.txt").exists()


async def test_rollback_removes_untracked(git_repo: Path):
    (git_repo / "untracked.txt").write_text("not tracked")
    result = await GitManager.rollback(git_repo, "cleanup")
    assert result.success is True
    assert not (git_repo / "untracked.txt").exists()


async def test_rollback_non_git_dir(tmp_path: Path):
    non_repo = tmp_path / "not-git"
    non_repo.mkdir()
    result = await GitManager.rollback(non_repo, "nothing to do")
    assert result.success is True


async def test_rollback_returns_changed_files(git_repo: Path):
    (git_repo / "file1.txt").write_text("a")
    (git_repo / "file2.txt").write_text("b")
    subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True, check=True)
    result = await GitManager.rollback(git_repo, "multi-file")
    assert result.success is True
    assert len(result.changed_files) >= 2
