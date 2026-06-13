import dataclasses

import pytest

from shannon_core.display.events import (
    AgentEvent,
    DisplayEvent,
    ErrorEvent,
    LlmTurnEvent,
    PhaseEvent,
    ResumeEvent,
    SummaryEvent,
    ToolCallEvent,
    WorkflowHeader,
)


def test_workflow_header_fields():
    e = WorkflowHeader(timestamp="2026-01-01 00:00:00", category="HEADER",
                       workflow_id="wf-1", target_url="https://x.com")
    assert e.workflow_id == "wf-1"
    assert e.target_url == "https://x.com"


def test_phase_event_literal():
    e = PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start")
    assert e.event == "start"


def test_agent_event_defaults():
    e = AgentEvent(timestamp="t", category="AGENT", agent_name="xss-vuln",
                   event="start", attempt=1)
    assert e.duration_ms is None
    assert e.cost_usd is None
    assert e.success is None


def test_tool_call_event():
    e = ToolCallEvent(timestamp="t", category="TOOL", agent_name="a",
                      tool_name="Bash", parameters={"command": "ls"})
    assert e.parameters == {"command": "ls"}


def test_error_event_has_classification_fields():
    e = ErrorEvent(timestamp="t", category="ERROR", error_type="ValueError",
                   message="boom")
    assert e.classified is None
    assert e.display_retryable is None


def test_summary_event():
    e = SummaryEvent(timestamp="t", category="SUMMARY", status="completed",
                     total_duration_ms=1000, total_cost_usd=0.5, agents=[])
    assert e.status == "completed"


def test_resume_event():
    e = ResumeEvent(timestamp="t", category="RESUME", previous_workflow_id="w1",
                    new_workflow_id="w2", checkpoint_hash="abc", completed_agents=["a"])
    assert e.completed_agents == ["a"]


def test_all_events_are_frozen():
    for ctor in [
        lambda: WorkflowHeader(timestamp="t", category="HEADER", workflow_id=None, target_url=None),
        lambda: PhaseEvent(timestamp="t", category="PHASE", phase="p", event="start"),
        lambda: AgentEvent(timestamp="t", category="AGENT", agent_name="a", event="start", attempt=1),
        lambda: ToolCallEvent(timestamp="t", category="TOOL", agent_name="a", tool_name="T", parameters={}),
        lambda: LlmTurnEvent(timestamp="t", category="LLM", agent_name="a", turn=1, content="c"),
        lambda: ErrorEvent(timestamp="t", category="ERROR", error_type="E", message="m"),
        lambda: SummaryEvent(timestamp="t", category="SUMMARY", status="completed",
                             total_duration_ms=1, total_cost_usd=0.0, agents=[]),
        lambda: ResumeEvent(timestamp="t", category="RESUME", previous_workflow_id="a",
                            new_workflow_id="b", checkpoint_hash="h", completed_agents=[]),
    ]:
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctor().timestamp = "mutated"
