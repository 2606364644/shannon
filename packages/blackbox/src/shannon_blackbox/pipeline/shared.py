from dataclasses import dataclass, field


@dataclass
class BlackboxPipelineInput:
    web_url: str
    workspace_name: str | None = None
    config_path: str | None = None
    output_path: str | None = None
    repo_path: str | None = None
    resume_from_workspace: str | None = None
    vuln_classes: list[str] | None = None
    exploit: bool = True
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    deliverables_subdir: str = ".shannon/deliverables"


@dataclass
class BlackboxPipelineState:
    status: str = "running"
    current_phase: str | None = None
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    has_whitebox_results: bool = False
    start_time: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass
class BlackboxActivityInput:
    web_url: str
    repo_path: str | None = None
    config_path: str | None = None
    workspace_name: str | None = None
    deliverables_subdir: str = ".shannon/deliverables"
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    agent_name: str | None = None
    vuln_type: str | None = None
    workspace_path: str | None = None
