from typing import Literal

from pydantic import BaseModel


class AgentEndResult(BaseModel):
    success: bool
    duration_ms: int
    cost_usd: float
    attempt_number: int = 1
    model: str | None = None
    error: str | None = None
    is_final_attempt: bool = True
    checkpoint: str | None = None


class AgentLogDetails(BaseModel):
    attempt_number: int = 1
    duration_ms: int | None = None
    cost_usd: float | None = None
    success: bool | None = None
    error: str | None = None


class AgentMetricsSummary(BaseModel):
    duration_ms: int
    cost_usd: float | None = None


class WorkflowSummary(BaseModel):
    status: Literal["completed", "failed", "cancelled"]
    total_duration_ms: int
    total_cost_usd: float
    completed_agents: list[str]
    agent_metrics: dict[str, AgentMetricsSummary]
    error: str | None = None


class ResumeInfo(BaseModel):
    previous_workflow_id: str
    new_workflow_id: str
    checkpoint_hash: str
    completed_agents: list[str]
