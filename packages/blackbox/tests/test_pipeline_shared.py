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
