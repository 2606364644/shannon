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
    ResumeEvent, StepEvent, SummaryEvent, ToolCallEvent,
)
from shannon_core.display.formatters import humanize_tool_call, first_nonempty_line

AgentStatus = Literal["running", "done", "failed"]


@dataclass(frozen=True)
class AgentRow:
    name: str
    status: AgentStatus = "running"
    attempt: int = 1
    turn: int = 0
    last_action: str | None = None
    last_action_detail: str | None = None
    last_turn_text: str | None = None
    duration_ms: int | None = None
    cost_usd: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class DashboardState:
    current_phase: str | None = None
    agents: dict[str, AgentRow] = field(default_factory=dict)
    phase_units: tuple[str, ...] = ()
    unit_status: dict[str, str] = field(default_factory=dict)
    unit_intent: dict[str, str] = field(default_factory=dict)

    @property
    def completed_count(self) -> int:
        return sum(1 for r in self.agents.values() if r.status in ("done", "failed"))

    @property
    def total_cost(self) -> float:
        return sum(r.cost_usd or 0.0 for r in self.agents.values())

    @property
    def total_units(self) -> int:
        return len(self.phase_units)

    @property
    def completed_units(self) -> int:
        return sum(1 for st in self.unit_status.values() if st in ("done", "failed"))

    @property
    def running_units(self) -> list[str]:
        return [n for n in self.phase_units if self.unit_status.get(n) == "running"]

    def _set_unit(self, name: str, status: str) -> "DashboardState":
        if name not in self.phase_units:
            return self   # unit not declared in this phase -> ignore (keeps agents clean)
        units = dict(self.unit_status)
        units[name] = status
        return replace(self, unit_status=units)

    def apply(self, event: DisplayEvent) -> "DashboardState":
        """Return a new state with the event folded in (immutable)."""
        if isinstance(event, PhaseEvent):
            if event.event == "start":
                intents = {n: i for n, i in zip(event.steps, event.step_intents) if i}
                return replace(self, current_phase=event.phase,
                               phase_units=event.steps, unit_status={}, unit_intent=intents)
            return replace(self, current_phase=event.phase)  # complete: keep units

        if isinstance(event, StepEvent):
            status = "running" if event.event == "start" else (
                "failed" if event.error else "done")
            state = self._set_unit(event.name, status)
            if event.intent:
                intents = dict(state.unit_intent)
                intents[event.name] = event.intent
                state = replace(state, unit_intent=intents)
            return state

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
                next_state = self._set_unit(event.agent_name, "running")
            else:  # end
                status: AgentStatus = "done" if event.success else "failed"
                agents[event.agent_name] = replace(
                    cur, status=status,
                    duration_ms=event.duration_ms if event.duration_ms is not None else cur.duration_ms,
                    cost_usd=event.cost_usd if event.cost_usd is not None else cur.cost_usd,
                    error=event.error)
                next_state = self._set_unit(event.agent_name, status)
            return replace(next_state, agents=agents)

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
                line = first_nonempty_line(event.content)
                agents[event.agent_name] = replace(
                    cur, turn=event.turn,
                    last_turn_text=line or cur.last_turn_text)
            return replace(self, agents=agents)

        # ErrorEvent, SummaryEvent, WorkflowHeader -> no dashboard-state change
        return self
