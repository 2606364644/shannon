import json
import os
from pathlib import Path

from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentEndResult
from shannon_whitebox.audit.utils import (
    format_timestamp,
    generate_session_json_path,
)


class MetricsTracker:
    """Manages session.json with atomic read/write."""

    def __init__(self, session_metadata: SessionMetadata):
        self._meta = session_metadata
        self._path = generate_session_json_path(session_metadata)
        self._data: dict = {}

    async def initialize(self, workflow_id: str | None = None) -> None:
        """Create the initial session.json structure."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        ts = format_timestamp()
        self._data = {
            "session": {
                "id": self._meta.id,
                "webUrl": self._meta.web_url,
                "status": "in-progress",
                "createdAt": ts,
                "originalWorkflowId": workflow_id,
                "resumeAttempts": [],
            },
            "metrics": {
                "total_duration_ms": 0,
                "total_cost_usd": 0,
                "phases": {},
                "agents": {},
            },
        }
        await self._atomic_write()

    def start_agent(self, agent_name: str, attempt_number: int) -> None:
        """Record that an agent has started (in-memory only)."""
        if agent_name not in self._data["metrics"]["agents"]:
            self._data["metrics"]["agents"][agent_name] = {
                "duration_ms": 0,
                "cost_usd": 0,
                "attempts": attempt_number,
            }

    async def end_agent(self, agent_name: str, result: AgentEndResult) -> None:
        """Persist agent results and update running totals."""
        agents = self._data["metrics"]["agents"]
        if agent_name not in agents:
            agents[agent_name] = {}
        agents[agent_name].update({
            "duration_ms": result.duration_ms,
            "cost_usd": result.cost_usd,
            "success": result.success,
            "attempt_number": result.attempt_number,
            "model": result.model,
        })
        if result.error:
            agents[agent_name]["error"] = result.error

        self._data["metrics"]["total_duration_ms"] += result.duration_ms
        self._data["metrics"]["total_cost_usd"] += result.cost_usd

        await self._atomic_write()

    async def update_session_status(self, status: str) -> None:
        """Update the session status field."""
        self._data["session"]["status"] = status
        await self._atomic_write()

    async def add_resume_attempt(self, workflow_id: str, terminated: list[str], checkpoint: str | None = None) -> None:
        """Append a resume attempt to the session record."""
        attempt = {
            "workflowId": workflow_id,
            "terminatedAgents": terminated,
            "checkpoint": checkpoint,
        }
        self._data["session"]["resumeAttempts"].append(attempt)
        await self._atomic_write()

    async def reload(self) -> None:
        """Reload session.json from disk (pick up external changes)."""
        if self._path.exists():
            content = self._path.read_text(encoding="utf-8")
            self._data = json.loads(content)

    def get_metrics(self) -> dict:
        """Return the current metrics dict."""
        return self._data.get("metrics", {})

    async def _atomic_write(self) -> None:
        """Write to a temp file then atomically replace session.json."""
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")
        os.replace(str(tmp), str(self._path))
