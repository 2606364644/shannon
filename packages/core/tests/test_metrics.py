from shannon_core.models.metrics import AgentMetrics, SessionMetadata
from shannon_core.models.audit import PhaseMetrics

def test_agent_metrics_defaults():
    m = AgentMetrics(duration_ms=1000)
    assert m.duration_ms == 1000
    assert m.input_tokens is None
    assert m.output_tokens is None
    assert m.cost_usd is None
    assert m.num_turns is None
    assert m.model is None
    assert m.structured_output is None

def test_agent_metrics_full():
    m = AgentMetrics(
        duration_ms=5000,
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.05,
        num_turns=10,
        model="claude-sonnet-4-6",
    )
    assert m.cost_usd == 0.05
    assert m.model == "claude-sonnet-4-6"

def test_session_metadata():
    s = SessionMetadata(id="test-123", web_url="https://example.com")
    assert s.id == "test-123"
    assert s.web_url == "https://example.com"
    assert s.repo_path is None
    assert s.output_path is None

def test_session_metadata_optional_web_url():
    s = SessionMetadata(id="test-123")
    assert s.id == "test-123"
    assert s.web_url is None


def test_session_metadata_extra_fields():
    s = SessionMetadata(id="test", web_url="https://x.com", custom_field="value")
    assert s.custom_field == "value"


def test_agent_metrics_structured_output_none():
    from shannon_core.models.metrics import AgentMetrics
    m = AgentMetrics(duration_ms=100)
    assert m.structured_output is None


def test_agent_metrics_structured_output_dict():
    from shannon_core.models.metrics import AgentMetrics
    m = AgentMetrics(
        duration_ms=100,
        structured_output={"login_success": True},
    )
    assert m.structured_output == {"login_success": True}


def test_agent_metrics_structured_output_nested():
    from shannon_core.models.metrics import AgentMetrics
    data = {
        "login_success": False,
        "failure_point": "totp_secret",
        "failure_detail": "TOTP code rejected",
    }
    m = AgentMetrics(duration_ms=200, structured_output=data)
    assert m.structured_output["failure_point"] == "totp_secret"


def test_phase_metrics_defaults():
    pm = PhaseMetrics()
    assert pm.duration_ms == 0
    assert pm.duration_percentage == 0.0
    assert pm.cost_usd == 0.0
    assert pm.agent_count == 0


def test_phase_metrics_with_values():
    pm = PhaseMetrics(
        duration_ms=15000,
        duration_percentage=12.5,
        cost_usd=0.10,
        agent_count=1,
    )
    assert pm.duration_ms == 15000
    assert pm.duration_percentage == 12.5
    assert pm.cost_usd == 0.10
    assert pm.agent_count == 1
