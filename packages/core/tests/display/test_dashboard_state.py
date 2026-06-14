from shannon_core.display.dashboard_state import DashboardState, AgentRow
from shannon_core.display.events import (
    PhaseEvent, AgentEvent, ToolCallEvent, LlmTurnEvent, ErrorEvent, ResumeEvent,
)


def _phase(name: str) -> PhaseEvent:
    return PhaseEvent(timestamp="t", category="PHASE", phase=name, event="start")


def _agent(name: str, event: str = "start", **kw) -> AgentEvent:
    return AgentEvent(timestamp="t", category="AGENT", agent_name=name,
                      event=event, attempt=kw.get("attempt", 1),
                      duration_ms=kw.get("duration_ms"), cost_usd=kw.get("cost_usd"),
                      success=kw.get("success"), error=kw.get("error"))


def test_initial_state_is_empty():
    s = DashboardState()
    assert s.current_phase is None
    assert s.agents == {}
    assert s.completed_count == 0
    assert s.total_cost == 0.0


def test_phase_event_sets_current_phase():
    s = DashboardState().apply(_phase("vulnerability-analysis"))
    assert s.current_phase == "vulnerability-analysis"


def test_agent_start_creates_running_row():
    s = DashboardState().apply(_agent("injection-vuln", "start", attempt=1))
    row = s.agents["injection-vuln"]
    assert row.status == "running"
    assert row.attempt == 1
    assert s.completed_count == 0


def test_agent_end_marks_done_and_counts():
    s = (DashboardState()
         .apply(_agent("injection-vuln", "start"))
         .apply(_agent("injection-vuln", "end", duration_ms=5200, cost_usd=0.15, success=True)))
    row = s.agents["injection-vuln"]
    assert row.status == "done"
    assert row.duration_ms == 5200
    assert row.cost_usd == 0.15
    assert s.completed_count == 1
    assert s.total_cost == 0.15


def test_agent_end_failed_marks_failed():
    s = (DashboardState()
         .apply(_agent("xss-vuln", "start"))
         .apply(_agent("xss-vuln", "end", success=False, error="rate limit")))
    assert s.agents["xss-vuln"].status == "failed"
    assert s.agents["xss-vuln"].error == "rate limit"
    assert s.completed_count == 1  # terminal either way


def test_retry_updates_attempt_and_back_to_running():
    s = (DashboardState()
         .apply(_agent("xss-vuln", "start", attempt=1))
         .apply(_agent("xss-vuln", "end", success=False, error="boom"))
         .apply(_agent("xss-vuln", "start", attempt=2)))
    assert s.agents["xss-vuln"].status == "running"
    assert s.agents["xss-vuln"].attempt == 2


def test_tool_call_updates_last_action_and_turn_is_unaffected():
    s = (DashboardState()
         .apply(_agent("injection-vuln", "start"))
         .apply(ToolCallEvent(timestamp="t", category="TOOL", agent_name="injection-vuln",
                              tool_name="Bash", parameters={"command": "rg -n eval"})))
    assert s.agents["injection-vuln"].last_action == "Bash"
    assert s.agents["injection-vuln"].last_action_detail == "command=rg -n eval"


def test_llm_turn_updates_turn_count():
    s = (DashboardState()
         .apply(_agent("injection-vuln", "start"))
         .apply(LlmTurnEvent(timestamp="t", category="LLM", agent_name="injection-vuln",
                             turn=3, content="...")))
    assert s.agents["injection-vuln"].turn == 3


def test_resume_event_seeds_completed_agents():
    s = DashboardState().apply(ResumeEvent(
        timestamp="t", category="RESUME", previous_workflow_id="a", new_workflow_id="b",
        checkpoint_hash="h", completed_agents=["recon", "pre-recon"]))
    assert s.agents["recon"].status == "done"
    assert s.agents["pre-recon"].status == "done"
    assert s.completed_count == 2


def test_apply_is_immutable():
    s0 = DashboardState()
    s1 = s0.apply(_phase("recon"))
    assert s0.current_phase is None  # original unchanged
    assert s1.current_phase == "recon"


def test_unknown_event_is_noop():
    from shannon_core.display.events import WorkflowHeader
    s = DashboardState().apply(WorkflowHeader(timestamp="t", category="HEADER",
                                              workflow_id="w", target_url="u"))
    assert s == DashboardState()
