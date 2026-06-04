import pytest
from shannon_blackbox.pipeline.shared import BlackboxPipelineState


def test_blackbox_pipeline_state_initializes_with_empty_errors_list():
    state = BlackboxPipelineState()
    assert hasattr(state, "errors")
    assert isinstance(state.errors, list)
    assert len(state.errors) == 0


def test_blackbox_pipeline_state_errors_can_be_appended():
    state = BlackboxPipelineState()
    state.errors.append("error1")
    state.errors.append("error2")
    assert len(state.errors) == 2
    assert state.errors == ["error1", "error2"]


def test_blackbox_pipeline_state_default_factory_creates_new_list_each_instance():
    state1 = BlackboxPipelineState()
    state2 = BlackboxPipelineState()
    state1.errors.append("error1")
    assert len(state2.errors) == 0


class TestBlackboxPipelineStateErrorPropagation:
    """Test new error_code and failed_agents fields on BlackboxPipelineState."""

    def test_error_code_defaults_to_none(self):
        state = BlackboxPipelineState()
        assert state.error_code is None

    def test_failed_agents_defaults_to_empty_list(self):
        state = BlackboxPipelineState()
        assert state.failed_agents == []
        assert isinstance(state.failed_agents, list)

    def test_failed_agents_can_be_appended(self):
        state = BlackboxPipelineState()
        state.failed_agents.append("injection-exploit")
        state.failed_agents.append("xss-exploit")
        assert state.failed_agents == ["injection-exploit", "xss-exploit"]

    def test_error_code_can_be_set(self):
        state = BlackboxPipelineState()
        state.error_code = "PermissionError"
        assert state.error_code == "PermissionError"

    def test_factory_isolation_for_failed_agents(self):
        """Each BlackboxPipelineState instance gets its own failed_agents list."""
        state1 = BlackboxPipelineState()
        state2 = BlackboxPipelineState()
        state1.failed_agents.append("agent-a")
        assert state2.failed_agents == []

    def test_status_failed_with_failed_agents(self):
        state = BlackboxPipelineState()
        state.status = "failed"
        state.failed_agents = ["injection-exploit"]
        state.errors = ["injection-exploit: connection refused"]
        state.error_code = "TransientError"
        assert state.status == "failed"
        assert len(state.failed_agents) == 1

    def test_status_cancelled(self):
        state = BlackboxPipelineState()
        state.status = "cancelled"
        assert state.status == "cancelled"
