from pydantic import BaseModel
from .metrics import AgentMetrics

class WhiteboxScanResult(BaseModel):
    status: str
    completed_agents: list[str]
    agent_metrics: dict[str, AgentMetrics]
    error: str | None = None
    workspace_path: str | None = None

class BlackboxScanResult(BaseModel):
    status: str
    completed_agents: list[str]
    agent_metrics: dict[str, AgentMetrics]
    has_whitebox_results: bool = False
    error: str | None = None
    workspace_path: str | None = None
