"""Shared base types for pipeline inputs."""

from dataclasses import dataclass, field

from shannon_core.utils.paths import get_default_deliverables_subdir


@dataclass
class BasePipelineInput:
    """Shared fields for whitebox and blackbox pipeline inputs."""
    config_path: str | None = None
    output_path: str | None = None
    workspace_name: str | None = None
    resume_from_workspace: str | None = None
    vuln_classes: list[str] | None = None      # Unified to str
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    deliverables_subdir: str = field(default_factory=get_default_deliverables_subdir)
