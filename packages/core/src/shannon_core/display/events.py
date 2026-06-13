"""DisplayEvent family — immutable pure-data representations of log activity.

The single source of truth: both renderers consume these events. Events carry
NO rendering logic, so they can be replayed, serialized, and tested in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class DisplayEvent:
    """Base for all display events."""

    timestamp: str
    category: str


@dataclass(frozen=True)
class WorkflowHeader(DisplayEvent):
    workflow_id: str | None
    target_url: str | None


@dataclass(frozen=True)
class PhaseEvent(DisplayEvent):
    phase: str
    event: Literal["start", "complete"]


@dataclass(frozen=True)
class AgentEvent(DisplayEvent):
    agent_name: str
    event: Literal["start", "end"]
    attempt: int
    duration_ms: int | None = None
    cost_usd: float | None = None
    success: bool | None = None
    error: str | None = None


@dataclass(frozen=True)
class ToolCallEvent(DisplayEvent):
    agent_name: str
    tool_name: str
    parameters: Any


@dataclass(frozen=True)
class LlmTurnEvent(DisplayEvent):
    agent_name: str
    turn: int
    content: str


@dataclass(frozen=True)
class ErrorEvent(DisplayEvent):
    error_type: str
    message: str
    context: str | None = None
    classified: str | None = None
    display_retryable: bool | None = None


@dataclass(frozen=True)
class AgentMetric:
    name: str
    duration_ms: int
    cost_usd: float | None = None
    success: bool = True


@dataclass(frozen=True)
class SummaryEvent(DisplayEvent):
    status: str
    total_duration_ms: int
    total_cost_usd: float
    agents: list[AgentMetric] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class ResumeEvent(DisplayEvent):
    previous_workflow_id: str
    new_workflow_id: str
    checkpoint_hash: str
    completed_agents: list[str] = field(default_factory=list)
