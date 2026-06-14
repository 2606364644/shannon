"""DashboardState — immutable pure-data state machine for the live dashboard.

Eats DisplayEvents and returns a new DashboardState. Holds NO rendering logic
and NO time calls, so it is fully deterministic and unit-testable in isolation.
Elapsed/clock is computed by the renderer at render time, not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from shannon_core.display.events import (
    AgentEvent, DisplayEvent, ErrorEvent, LlmTurnEvent, PhaseEvent,
    ResumeEvent, SummaryEvent, ToolCallEvent,
)
from shannon_core.display.formatters import humanize_tool_call

AgentStatus = Literal["running", "done", "failed"]


@dataclass(frozen=True)
class AgentRow:
    name: str
    status: AgentStatus = "running"
    attempt: int = 1
    turn: int = 0
    last_action: str | None = None
    last_action_detail: str | None = None
    duration_ms: int | None = None
    cost_usd: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class DashboardState:
    current_phase: str | None = None
    agents: dict[str, AgentRow] = field(default_factory=dict)

    @property
    def completed_count(self) -> int:
        return sum(1 for r in self.agents.values() if r.status in ("done", "failed"))

    @property
    def total_cost(self) -> float:
        return sum(r.cost_usd or 0.0 for r in self.agents.values())

    def apply(self, event: DisplayEvent) -> "DashboardState":
        """Return a new state with the event folded in (immutable)."""
        if isinstance(event, PhaseEvent):
            return replace(self, current_phase=event.phase)

        if isinstance(event, ResumeEvent):
            agents = dict(self.agents)
            for name in event.completed_agents:
                agents[name] = AgentRow(name=name, status="done", attempt=1)
            return replace(self, agents=agents)

        if isinstance(event, AgentEvent):
            agents = dict(self.agents)
            cur = agents.get(event.agent_name, AgentRow(name=event.agent_name))
            if event.event == "start":
                agents[event.agent_name] = replace(
                    cur, status="running", attempt=event.attempt, error=None)
            else:  # end
                status: AgentStatus = "done" if event.success else "failed"
                agents[event.agent_name] = replace(
                    cur, status=status,
                    duration_ms=event.duration_ms if event.duration_ms is not None else cur.duration_ms,
                    cost_usd=event.cost_usd if event.cost_usd is not None else cur.cost_usd,
                    error=event.error)
            return replace(self, agents=agents)

        if isinstance(event, ToolCallEvent):
            agents = dict(self.agents)
            cur = agents.get(event.agent_name)
            if cur is not None:
                detail = humanize_tool_call(event.tool_name, event.parameters or {})
                agents[event.agent_name] = replace(
                    cur, last_action=event.tool_name, last_action_detail=detail)
            return replace(self, agents=agents)

        if isinstance(event, LlmTurnEvent):
            agents = dict(self.agents)
            cur = agents.get(event.agent_name)
            if cur is not None:
                agents[event.agent_name] = replace(cur, turn=event.turn)
            return replace(self, agents=agents)

        # ErrorEvent, SummaryEvent, WorkflowHeader -> no dashboard-state change
        return self
