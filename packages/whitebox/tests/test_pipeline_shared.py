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
