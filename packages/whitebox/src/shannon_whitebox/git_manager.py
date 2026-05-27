import subprocess
from pathlib import Path

from shannon_core.models.agents import AgentName
from shannon_core.models.errors import ErrorCode, PentestError

class GitManager:
    @staticmethod
    def _run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        return result

    @staticmethod
    def create_checkpoint(repo_path: Path, agent_name: str | AgentName, attempt: int = 1) -> None:
        name = agent_name.value if isinstance(agent_name, AgentName) else agent_name
        result = GitManager._run_git(repo_path, "add", "-A")
        result = GitManager._run_git(repo_path, "commit", "-m", f"checkpoint: before {name} (attempt {attempt})", "--allow-empty")
        if result.returncode != 0:
            raise PentestError(
                f"Git checkpoint failed for {name}: {result.stderr}",
                "filesystem",
                error_code=ErrorCode.GIT_CHECKPOINT_FAILED,
            )

    @staticmethod
    def commit(repo_path: Path, agent_name: str | AgentName) -> None:
        name = agent_name.value if isinstance(agent_name, AgentName) else agent_name
        GitManager._run_git(repo_path, "add", "-A")
        result = GitManager._run_git(repo_path, "commit", "-m", f"deliverable: {name}", "--allow-empty")
        if result.returncode != 0:
            raise PentestError(
                f"Git commit failed for {name}: {result.stderr}",
                "filesystem",
                error_code=ErrorCode.GIT_CHECKPOINT_FAILED,
            )

    @staticmethod
    def rollback(repo_path: Path, reason: str) -> None:
        GitManager._run_git(repo_path, "reset", "--hard", "HEAD")
        result = GitManager._run_git(repo_path, "clean", "-fd")
        if result.returncode != 0:
            raise PentestError(
                f"Git rollback failed: {result.stderr}",
                "filesystem",
                error_code=ErrorCode.GIT_ROLLBACK_FAILED,
                context={"reason": reason},
            )

    @staticmethod
    def get_commit_hash(repo_path: Path) -> str | None:
        result = GitManager._run_git(repo_path, "rev-parse", "HEAD")
        if result.returncode == 0:
            return result.stdout.strip()
        return None
