import pytest
from shannon_whitebox.pipeline.shared import PipelineState


def test_pipeline_state_initializes_with_empty_errors_list():
    state = PipelineState()
    assert hasattr(state, "errors")
    assert isinstance(state.errors, list)
    assert len(state.errors) == 0


def test_pipeline_state_errors_can_be_appended():
    state = PipelineState()
    state.errors.append("error1")
    state.errors.append("error2")
    assert len(state.errors) == 2
    assert state.errors == ["error1", "error2"]


def test_pipeline_state_default_factory_creates_new_list_each_instance():
    state1 = PipelineState()
    state2 = PipelineState()
    state1.errors.append("error1")
    assert len(state2.errors) == 0


class TestPipelineStateErrorPropagation:
    """Test new error_code and failed_agents fields on PipelineState."""

    def test_error_code_defaults_to_none(self):
        state = PipelineState()
        assert state.error_code is None

    def test_failed_agents_defaults_to_empty_list(self):
        state = PipelineState()
        assert state.failed_agents == []
        assert isinstance(state.failed_agents, list)

    def test_failed_agents_can_be_appended(self):
        state = PipelineState()
        state.failed_agents.append("xss-vuln")
        state.failed_agents.append("sqli-vuln")
        assert state.failed_agents == ["xss-vuln", "sqli-vuln"]

    def test_error_code_can_be_set(self):
        state = PipelineState()
        state.error_code = "AuthenticationError"
        assert state.error_code == "AuthenticationError"

    def test_factory_isolation_for_failed_agents(self):
        """Each PipelineState instance gets its own failed_agents list."""
        state1 = PipelineState()
        state2 = PipelineState()
        state1.failed_agents.append("agent-a")
        assert state2.failed_agents == []

    def test_status_failed_with_failed_agents(self):
        """Workflow can set status=failed when agents fail."""
        state = PipelineState()
        state.status = "failed"
        state.failed_agents = ["xss-vuln"]
        state.errors = ["xss-vuln: timeout"]
        state.error_code = "TransientError"
        assert state.status == "failed"
        assert len(state.failed_agents) == 1
        assert state.error_code == "TransientError"

    def test_status_cancelled(self):
        """Workflow can set status=cancelled."""
        state = PipelineState()
        state.status = "cancelled"
        assert state.status == "cancelled"
