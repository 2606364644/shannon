from shannon_core.models.metrics import AgentMetrics, SessionMetadata

def test_agent_metrics_defaults():
    m = AgentMetrics(duration_ms=1000)
    assert m.duration_ms == 1000
    assert m.input_tokens is None
    assert m.output_tokens is None
    assert m.cost_usd is None
    assert m.num_turns is None
    assert m.model is None

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

def test_session_metadata_extra_fields():
    s = SessionMetadata(id="test", web_url="https://x.com", custom_field="value")
    assert s.custom_field == "value"
