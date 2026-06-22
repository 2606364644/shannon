"""PipelineInput.max_concurrent field tests (mirrors blackbox test_workflows.py)."""

from shannon_whitebox.pipeline.shared import PipelineInput


def test_pipeline_input_max_concurrent_default():
    """Default max_concurrent should be 3."""
    input = PipelineInput(repo_path="/fake/repo")
    assert input.max_concurrent == 3


def test_pipeline_input_max_concurrent_custom():
    """Custom max_concurrent should be respected."""
    input = PipelineInput(repo_path="/fake/repo", max_concurrent=2)
    assert input.max_concurrent == 2
