import pytest
import subprocess
from pathlib import Path
from shannon_whitebox.git_manager import GitManager

@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
    (repo / "initial.txt").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True, check=True)
    return repo

def test_create_checkpoint(git_repo):
    GitManager.create_checkpoint(git_repo, "pre-recon", 1)

def test_commit_success(git_repo):
    (git_repo / "deliverable.md").write_text("# Report")
    GitManager.commit(git_repo, "pre-recon")
    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=git_repo, capture_output=True, text=True, check=True,
    )
    assert "pre-recon" in result.stdout

def test_rollback(git_repo):
    (git_repo / "bad_file.txt").write_text("bad content")
    subprocess.run(["git", "add", "."], cwd=git_repo, capture_output=True, check=True)
    GitManager.rollback(git_repo, "test failure")
    assert not (git_repo / "bad_file.txt").exists()

def test_get_commit_hash(git_repo):
    h = GitManager.get_commit_hash(git_repo)
    assert h is not None
    assert len(h) == 40
