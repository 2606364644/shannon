from shannon_core.models.audit import (
    AgentEndResult,
    AgentLogDetails,
    AgentMetricsSummary,
    WorkflowSummary,
    ResumeInfo,
)


def test_agent_end_result_defaults():
    r = AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05)
    assert r.success is True
    assert r.duration_ms == 5000
    assert r.cost_usd == 0.05
    assert r.attempt_number == 1
    assert r.model is None
    assert r.error is None
    assert r.is_final_attempt is True
    assert r.checkpoint is None


def test_agent_end_result_with_error():
    r = AgentEndResult(
        success=False,
        duration_ms=1000,
        cost_usd=0.01,
        attempt_number=3,
        error="Rate limit exceeded",
        is_final_attempt=False,
    )
    assert r.error == "Rate limit exceeded"
    assert r.is_final_attempt is False
    assert r.attempt_number == 3


def test_agent_log_details_defaults():
    d = AgentLogDetails()
    assert d.attempt_number == 1
    assert d.duration_ms is None
    assert d.cost_usd is None
    assert d.success is None
    assert d.error is None


def test_agent_metrics_summary():
    m = AgentMetricsSummary(duration_ms=30000, cost_usd=0.05)
    assert m.duration_ms == 30000
    assert m.cost_usd == 0.05


def test_agent_metrics_summary_no_cost():
    m = AgentMetricsSummary(duration_ms=1000)
    assert m.cost_usd is None


def test_workflow_summary_completed():
    s = WorkflowSummary(
        status="completed",
        total_duration_ms=300000,
        total_cost_usd=0.12,
        completed_agents=["recon", "injection-vuln"],
        agent_metrics={
            "recon": AgentMetricsSummary(duration_ms=150000, cost_usd=0.06),
            "injection-vuln": AgentMetricsSummary(duration_ms=150000, cost_usd=0.06),
        },
    )
    assert s.status == "completed"
    assert len(s.completed_agents) == 2
    assert s.error is None


def test_workflow_summary_failed_with_error():
    s = WorkflowSummary(
        status="failed",
        total_duration_ms=10000,
        total_cost_usd=0.01,
        completed_agents=[],
        agent_metrics={},
        error="Agent crashed",
    )
    assert s.status == "failed"
    assert s.error == "Agent crashed"


def test_resume_info():
    r = ResumeInfo(
        previous_workflow_id="wf-old",
        new_workflow_id="wf-new",
        checkpoint_hash="abc123",
        completed_agents=["recon"],
    )
    assert r.previous_workflow_id == "wf-old"
    assert r.completed_agents == ["recon"]
