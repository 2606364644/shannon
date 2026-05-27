import json
from pathlib import Path

from shannon_core.models.agents import AgentName
from shannon_core.models.metrics import AgentMetrics

from .log_stream import LogStream

class AuditSession:
    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        self.workflow_log = LogStream(workspace_path / "workflow.log")
        self.agents_dir = workspace_path / "agents"
        self.agents_dir.mkdir(exist_ok=True)
        self.prompts_dir = workspace_path / "prompts"
        self.prompts_dir.mkdir(exist_ok=True)
        self._current_agent: str | None = None

    async def log(self, message: str) -> None:
        await self.workflow_log.append(message)

    async def log_phase(self, phase: str, status: str) -> None:
        await self.workflow_log.append(f"Phase {phase}: {status}")

    async def start_agent(self, agent_name: AgentName, prompt: str, attempt: int = 1) -> None:
        self._current_agent = agent_name.value
        agent_log = LogStream(self.agents_dir / f"{agent_name.value}.log")
        await agent_log.append(f"Started (attempt {attempt})")

        prompt_path = self.prompts_dir / f"{agent_name.value}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")

    async def end_agent(self, agent_name: AgentName, success: bool, metrics: AgentMetrics | None = None) -> None:
        status = "completed" if success else "failed"
        agent_log = LogStream(self.agents_dir / f"{agent_name.value}.log")
        await agent_log.append(f"Status: {status}")
        if metrics:
            await agent_log.append(f"Duration: {metrics.duration_ms}ms, Cost: ${metrics.cost_usd or 0:.2f}")
        self._current_agent = None

    async def save_session(self, session_data: dict) -> None:
        session_path = self.workspace_path / "session.json"
        session_path.write_text(json.dumps(session_data, indent=2, default=str), encoding="utf-8")
