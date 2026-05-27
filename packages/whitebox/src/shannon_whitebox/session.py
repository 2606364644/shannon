import json
import time
from pathlib import Path

from shannon_core.models.agents import AgentName

class SessionManager:
    def __init__(self, workspaces_dir: Path):
        self.workspaces_dir = workspaces_dir
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

    def create_workspace(self, web_url: str, repo_path: str, name: str | None = None) -> Path:
        if not name:
            hostname = web_url.replace("https://", "").replace("http://", "").split("/")[0].replace(".", "-")
            session_id = f"shannon-{int(time.time() * 1000)}"
            name = f"{hostname}_{session_id}"

        ws = self.workspaces_dir / name
        ws.mkdir(parents=True, exist_ok=True)

        session_data = {
            "web_url": web_url,
            "repo_path": repo_path,
            "created_at": time.time(),
            "completed_agents": [],
            "metrics": {"agents": {}},
        }
        (ws / "session.json").write_text(json.dumps(session_data, indent=2), encoding="utf-8")
        return ws

    def list_workspaces(self) -> list[Path]:
        if not self.workspaces_dir.exists():
            return []
        return sorted(
            [p for p in self.workspaces_dir.iterdir() if p.is_dir() and (p / "session.json").exists()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    def get_workspace(self, name: str) -> Path | None:
        ws = self.workspaces_dir / name
        if ws.exists() and (ws / "session.json").exists():
            return ws
        return None

    def get_session_data(self, workspace_path: Path) -> dict:
        session_file = workspace_path / "session.json"
        if not session_file.exists():
            return {}
        return json.loads(session_file.read_text(encoding="utf-8"))

    def update_session(self, workspace_path: Path, data: dict) -> None:
        existing = self.get_session_data(workspace_path)
        existing.update(data)
        (workspace_path / "session.json").write_text(
            json.dumps(existing, indent=2, default=str), encoding="utf-8",
        )

    def mark_agent_completed(self, workspace_path: Path, agent_name: AgentName) -> None:
        data = self.get_session_data(workspace_path)
        completed = data.get("completed_agents", [])
        if agent_name.value not in completed:
            completed.append(agent_name.value)
        data["completed_agents"] = completed
        self.update_session(workspace_path, data)

    def is_agent_completed(self, workspace_path: Path, agent_name: AgentName) -> bool:
        data = self.get_session_data(workspace_path)
        return agent_name.value in data.get("completed_agents", [])
