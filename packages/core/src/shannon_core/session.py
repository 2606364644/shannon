import json
import shutil
import time
from pathlib import Path

from shannon_core.models.agents import AgentName

class SessionManager:
    def __init__(self, workspaces_dir: Path):
        self.workspaces_dir = workspaces_dir
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

    def create_workspace(self, web_url: str, repo_path: str, name: str | None = None, *, scan_type: str = "whitebox") -> Path:
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
            "scan_type": scan_type,
            "status": "running",
            "completed_at": None,
            "links": {"parent_workspace": None, "child_workspaces": []},
            "deliverables_summary": None,
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

    def get_scan_type(self, workspace_path: Path) -> str:
        """Read scan_type from session.json, inferring from workspace name as fallback."""
        data = self.get_session_data(workspace_path)
        if "scan_type" in data:
            return data["scan_type"]
        session = data.get("session", {})
        if "scan_type" in session:
            return session["scan_type"]
        name = workspace_path.name.lower()
        if "blackbox" in name:
            return "blackbox"
        return "whitebox"

    def get_status(self, workspace_path: Path) -> str:
        """Read status from session.json, handling both flat and nested formats."""
        data = self.get_session_data(workspace_path)
        if "status" in data:
            return data["status"]
        session = data.get("session", {})
        if "status" in session:
            return session["status"]
        metrics = data.get("metrics", {})
        agents = metrics.get("agents", {})
        if agents:
            return "completed"
        return "unknown"

    def get_web_url(self, workspace_path: Path) -> str | None:
        """Read web_url from session.json, handling both flat and nested formats."""
        data = self.get_session_data(workspace_path)
        if "web_url" in data:
            return data["web_url"]
        session = data.get("session", {})
        return session.get("webUrl") or session.get("web_url")

    def get_created_at(self, workspace_path: Path) -> float | None:
        """Read created_at timestamp from session.json, handling both formats."""
        data = self.get_session_data(workspace_path)
        if "created_at" in data:
            return data["created_at"]
        session = data.get("session", {})
        return session.get("createdAt") or session.get("created_at")

    def get_completed_at(self, workspace_path: Path) -> float | None:
        """Read completed_at timestamp from session.json."""
        data = self.get_session_data(workspace_path)
        if "completed_at" in data:
            return data["completed_at"]
        session = data.get("session", {})
        return session.get("completedAt") or session.get("completed_at")

    def get_links(self, workspace_path: Path) -> dict:
        """Read links from session.json, returning defaults if absent."""
        data = self.get_session_data(workspace_path)
        if "links" in data:
            return data["links"]
        return {"parent_workspace": None, "child_workspaces": []}

    def set_parent_workspace(self, workspace_path: Path, parent_name: str) -> None:
        """Set the parent workspace link for a black-box workspace."""
        links = self.get_links(workspace_path)
        links["parent_workspace"] = parent_name
        self.update_session(workspace_path, {"links": links})

    def add_child_workspace(self, workspace_path: Path, child_name: str) -> None:
        """Add a child workspace link to a white-box workspace."""
        links = self.get_links(workspace_path)
        children = links.get("child_workspaces", [])
        if child_name not in children:
            children.append(child_name)
        links["child_workspaces"] = children
        self.update_session(workspace_path, {"links": links})

    def mark_completed(self, workspace_path: Path) -> None:
        """Mark workspace status as completed with timestamp."""
        self.update_session(workspace_path, {
            "status": "completed",
            "completed_at": time.time(),
        })

    def delete_workspace(self, workspace_name: str) -> bool:
        """Delete a workspace directory and handle parent-child links.

        Returns True if deleted, False if workspace not found.
        """
        ws = self.get_workspace(workspace_name)
        if ws is None:
            return False
        self._handle_workspace_links(ws)
        shutil.rmtree(ws)
        return True

    def _handle_workspace_links(self, workspace_path: Path) -> None:
        """Update linked workspaces before deleting this one."""
        data = self.get_session_data(workspace_path)
        scan_type = data.get("scan_type", "")
        links = data.get("links", {})
        workspace_name = workspace_path.name

        if scan_type == "whitebox":
            # Remove parent ref from each child
            for child_name in links.get("child_workspaces", []):
                child_ws = self.get_workspace(child_name)
                if child_ws is not None:
                    child_links = self.get_links(child_ws)
                    child_links["parent_workspace"] = None
                    self.update_session(child_ws, {"links": child_links})

        elif scan_type == "blackbox":
            # Remove this child from parent's child list
            parent_name = links.get("parent_workspace")
            if parent_name:
                parent_ws = self.get_workspace(parent_name)
                if parent_ws is not None:
                    parent_links = self.get_links(parent_ws)
                    children = parent_links.get("child_workspaces", [])
                    if workspace_name in children:
                        children.remove(workspace_name)
                    parent_links["child_workspaces"] = children
                    self.update_session(parent_ws, {"links": parent_links})
