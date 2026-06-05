from shannon_core.models.base import BasePipelineInput
from shannon_whitebox.pipeline.shared import PipelineInput
from shannon_blackbox.pipeline.shared import BlackboxPipelineInput


def test_pipeline_input_inherits_base():
    assert issubclass(PipelineInput, BasePipelineInput)


def test_blackbox_pipeline_input_inherits_base():
    assert issubclass(BlackboxPipelineInput, BasePipelineInput)


def test_base_has_shared_fields():
    """BasePipelineInput must have all shared fields."""
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(BasePipelineInput)}
    expected = {
        "config_path", "output_path", "workspace_name",
        "resume_from_workspace", "vuln_classes", "pipeline_testing_mode",
        "api_key", "deliverables_subdir",
    }
    assert expected == field_names
