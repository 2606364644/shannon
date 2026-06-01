from dataclasses import dataclass, field

from shannon_core.models.agents import VulnType
from shannon_core.models.metrics import AgentMetrics

@dataclass
class PipelineInput:
    repo_path: str
    web_url: str = ""
    config_path: str | None = None
    output_path: str | None = None
    workspace_name: str | None = None
    resume_from_workspace: str | None = None
    vuln_classes: list[VulnType] | None = None
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    deliverables_subdir: str = ".shannon/deliverables"
    prompt_override: str | None = None

@dataclass
class PipelineState:
    status: str = "running"
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    start_time: float = 0.0
    errors: list[str] = field(default_factory=list)
    code_index_stats: dict | None = None

@dataclass
class ActivityInput:
    repo_path: str
    web_url: str = ""
    config_path: str | None = None
    workspace_name: str | None = None
    deliverables_subdir: str = ".shannon/deliverables"
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    prompt_override: str | None = None
    workspace_path: str | None = None
