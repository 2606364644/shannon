"""Shared base types for pipeline inputs."""

import os
from dataclasses import dataclass, field

from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR


def _get_default_deliverables_subdir() -> str:
    """从环境变量获取默认产出物子目录。"""
    return os.getenv("SHANNON_DELIVERABLES_SUBDIR", DEFAULT_DELIVERABLES_SUBDIR)


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
    deliverables_subdir: str = field(default_factory=_get_default_deliverables_subdir)
