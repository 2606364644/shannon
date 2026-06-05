from dataclasses import dataclass, field

from shannon_core.models.base import BasePipelineInput
from shannon_core.constants import DEFAULT_DELIVERABLES_SUBDIR


@dataclass
class PipelineInput(BasePipelineInput):
    """Whitebox-specific fields.

    Note: vuln_classes accepts list[str] from the base class.
    Internally, VulnType enum values are used for type safety;
    conversion happens at the boundary (workflow entry).
    """
    repo_path: str = ""                        # Required for whitebox
    web_url: str = ""
    prompt_override: str | None = None


@dataclass
class PipelineState:
    status: str = "running"
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    start_time: float = 0.0
    errors: list[str] = field(default_factory=list)
    code_index_stats: dict | None = None
    audit_plan_stats: dict | None = None
    error_code: str | None = None
    failed_agents: list[str] = field(default_factory=list)


@dataclass
class ActivityInput:
    repo_path: str
    web_url: str = ""
    config_path: str | None = None
    workspace_name: str | None = None
    deliverables_subdir: str = DEFAULT_DELIVERABLES_SUBDIR
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    prompt_override: str | None = None
    workspace_path: str | None = None
