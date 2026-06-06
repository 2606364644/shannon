import os

from shannon_core.models.base import BasePipelineInput
from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR
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


def test_deliverables_subdir_uses_default_without_env():
    """Without SHANNON_DELIVERABLES_SUBDIR env var, uses DEFAULT_DELIVERABLES_SUBDIR."""
    old = os.environ.pop("SHANNON_DELIVERABLES_SUBDIR", None)
    try:
        inp = BasePipelineInput()
        assert inp.deliverables_subdir == DEFAULT_DELIVERABLES_SUBDIR
    finally:
        if old is not None:
            os.environ["SHANNON_DELIVERABLES_SUBDIR"] = old


def test_deliverables_subdir_uses_env_when_set(monkeypatch):
    """With SHANNON_DELIVERABLES_SUBDIR env var set, uses its value."""
    monkeypatch.setenv("SHANNON_DELIVERABLES_SUBDIR", "custom/path")
    inp = BasePipelineInput()
    assert inp.deliverables_subdir == "custom/path"


def test_deliverables_subdir_can_be_overridden():
    """Explicit value takes precedence over env var."""
    inp = BasePipelineInput(deliverables_subdir="explicit/path")
    assert inp.deliverables_subdir == "explicit/path"
