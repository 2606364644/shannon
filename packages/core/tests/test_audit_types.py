from supernova_core.models.audit import (
    AgentEndResult,
    AgentLogDetails,
    AgentMetricsSummary,
    WorkflowSummary,
    ResumeInfo,
    end_result_from_pentest_error,
)
from supernova_core.models.errors import PentestError


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


def test_agent_end_result_has_num_turns_field():
    """B2 观测:AgentEndResult 记录 turn 消耗(默认 None,向后兼容)。"""
    r = AgentEndResult(success=True, duration_ms=100, cost_usd=0.0, attempt_number=1)
    assert r.num_turns is None  # 默认 None,既有调用零破坏
    r2 = AgentEndResult(success=True, duration_ms=100, cost_usd=0.0, attempt_number=1, num_turns=42)
    assert r2.num_turns == 42


def test_agent_end_result_has_cost_and_token_fields():
    """cost 定价(spec 2026-07-09): AgentEndResult 带 cost_currency + 4 档 token。"""
    r = AgentEndResult(
        success=True, duration_ms=10, cost_usd=1.5, cost_currency="CNY",
        input_tokens=100, output_tokens=50, cache_read_tokens=10, cache_creation_tokens=0,
    )
    assert r.cost_currency == "CNY"
    assert r.input_tokens == 100
    assert r.cache_creation_tokens == 0


def test_audit_types_cost_currency_default_usd():
    """各类 cost_currency 默认 USD + WorkflowSummary token 汇总默认 0（向后兼容旧 session）。"""
    assert AgentEndResult(success=True, duration_ms=1, cost_usd=0.0).cost_currency == "USD"
    assert AgentLogDetails().cost_currency == "USD"
    assert AgentMetricsSummary(duration_ms=1).cost_currency == "USD"
    s = WorkflowSummary(status="completed", total_duration_ms=1, total_cost_usd=0.0,
                        completed_agents=[], agent_metrics={})
    assert s.cost_currency == "USD"
    assert s.total_input_tokens == 0


def test_end_result_from_pentest_error_carries_cost_context():
    """L3：PentestError.context 携带的 cost（executor L2 塞入）→ AgentEndResult，
    供 activities 失败分支记进 metrics（修 error path cost 归 0）。"""
    e = PentestError("boom", "validation", context={
        "cost_usd": 0.5, "cost_currency": "CNY", "model": "glm-5.2",
        "num_turns": 3, "input_tokens": 100, "output_tokens": 50,
        "cache_read_tokens": 10, "cache_creation_tokens": 5,
    })
    r = end_result_from_pentest_error(e, duration_ms=1234, attempt_number=2)
    assert r.success is False
    assert r.duration_ms == 1234
    assert r.attempt_number == 2
    assert r.cost_usd == 0.5
    assert r.cost_currency == "CNY"
    assert r.model == "glm-5.2"
    assert r.num_turns == 3
    assert r.input_tokens == 100
    assert r.output_tokens == 50
    assert r.cache_read_tokens == 10
    assert r.cache_creation_tokens == 5
    assert r.error == "boom"


def test_end_result_from_pentest_error_defaults_zero_when_no_context():
    """非 executor raise（无 context，如纯 IO probe 异常）→ cost 回落 0（真无法知道）。"""
    e = PentestError("io fail", "validation")  # 无 context → 默认 {}
    r = end_result_from_pentest_error(e, duration_ms=100, attempt_number=1)
    assert r.cost_usd == 0.0
    assert r.cost_currency == "USD"
    assert r.model is None
    assert r.num_turns is None
