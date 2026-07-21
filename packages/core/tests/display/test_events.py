import dataclasses

import pytest

from supernova_core.display.events import (
    AgentEvent,
    DisplayEvent,
    ErrorEvent,
    LlmTurnEvent,
    PhaseEvent,
    ResumeEvent,
    StepEvent,
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


def test_step_event_fields():
    e = StepEvent(timestamp="t", category="STEP", name="code-index",
                  phase="pre-recon", event="start")
    assert e.name == "code-index"
    assert e.phase == "pre-recon"
    assert e.duration_ms is None
    assert e.error is None


def test_phase_event_has_optional_steps_default_empty():
    e = PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start")
    assert e.steps == ()


def test_phase_event_carries_steps():
    e = PhaseEvent(timestamp="t", category="PHASE", phase="pre-recon", event="start",
                   steps=("code-index", "pre-recon"))
    assert e.steps == ("code-index", "pre-recon")


def test_workflow_header_banner_fields():
    e = WorkflowHeader(timestamp="t", category="HEADER", workflow_id="wf-1",
                       target_url=None, repo_path="/repo", mode="offline",
                       web_ui_url="http://localhost:8233/x", logs_cmd="logs wf --follow",
                       workspace="wf-1")
    assert e.repo_path == "/repo"
    assert e.mode == "offline"
    assert e.web_ui_url.startswith("http://localhost:8233")
    assert e.logs_cmd == "logs wf --follow"
    assert e.workspace == "wf-1"


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
        lambda: StepEvent(timestamp="t", category="STEP", name="x", phase="p", event="start"),
    ]:
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctor().timestamp = "mutated"


def test_step_event_carries_optional_intent():
    e = StepEvent(timestamp="t", category="STEP", name="code-index",
                  phase="pre-recon", event="start", intent="构建调用图与代码索引")
    assert e.intent == "构建调用图与代码索引"


def test_step_event_intent_defaults_none():
    e = StepEvent(timestamp="t", category="STEP", name="x", phase="p", event="start")
    assert e.intent is None


def test_phase_event_carries_step_intents():
    e = PhaseEvent(timestamp="t", category="PHASE", phase="pre-recon", event="start",
                   steps=("code-index", "pre-recon"),
                   step_intents=("构建调用图与代码索引", "扫描架构与入口点"))
    assert e.step_intents == ("构建调用图与代码索引", "扫描架构与入口点")


def test_phase_event_step_intents_default_empty():
    e = PhaseEvent(timestamp="t", category="PHASE", phase="recon", event="start")
    assert e.step_intents == ()


def test_info_event_defaults_to_info_level():
    from supernova_core.display.events import InfoEvent
    e = InfoEvent(timestamp="2026-06-28 12:00:00", category="INFO", message="hello")
    assert e.message == "hello"
    assert e.level == "info"


def test_info_event_warning_level():
    from supernova_core.display.events import InfoEvent
    e = InfoEvent(timestamp="t", category="INFO", message="careful", level="warning")
    assert e.level == "warning"


def test_cost_currency_and_token_fields_on_events():
    """cost 定价(spec 2026-07-09): AgentEvent/AgentMetric/SummaryEvent 带 cost_currency + token。

    asdict 断言覆盖 StructuredEventRenderer→events.ndjson 的序列化路径（Web SSE 读它）。
    """
    from supernova_core.display.events import AgentMetric

    ae = AgentEvent(timestamp="t", category="AGENT", agent_name="recon", event="end",
                    attempt=1, cost_usd=0.5, cost_currency="CNY", input_tokens=1000,
                    output_tokens=500, cache_read_tokens=100, cache_creation_tokens=0)
    assert ae.cost_currency == "CNY"
    assert ae.input_tokens == 1000
    assert dataclasses.asdict(ae)["cost_currency"] == "CNY"

    am = AgentMetric(name="recon", duration_ms=100, cost_usd=0.5, cost_currency="CNY",
                     input_tokens=1000, cache_read_tokens=100)
    assert am.cost_currency == "CNY"

    se = SummaryEvent(timestamp="t", category="SUMMARY", status="completed",
                      total_duration_ms=100, total_cost_usd=1.5, cost_currency="CNY",
                      agents=[am], total_input_tokens=1000, total_cache_read_tokens=100)
    assert se.cost_currency == "CNY"
    assert se.total_input_tokens == 1000
    sd = dataclasses.asdict(se)
    assert sd["cost_currency"] == "CNY"
    assert sd["agents"][0]["cost_currency"] == "CNY"


def test_event_cost_currency_defaults_usd():
    """未传 cost_currency → 默认 USD（向后兼容旧链路）。"""
    ae = AgentEvent(timestamp="t", category="AGENT", agent_name="a", event="start", attempt=1)
    assert ae.cost_currency == "USD"
    se = SummaryEvent(timestamp="t", category="SUMMARY", status="completed",
                      total_duration_ms=1, total_cost_usd=0.0, agents=[])
    assert se.cost_currency == "USD"
