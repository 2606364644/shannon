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
    resume_completed_agents: list[str] = field(default_factory=list)  # resume 预填
    max_concurrent: int = 3                    # SHANNON_MAX_CONCURRENT 注入;vuln agents 并发上限
    enable_llm_track: bool = True              # SHANNON_LLM_TRACK_ENABLED 注入;False=只跑 GitNexus 轨
    event_file: str | None = None              # C1: web 提交端塞 events.ndjson 路径(env 不跨容器); CLI 为 None 走 env 兜底


@dataclass
class PipelineState:
    status: str = "running"
    current_phase: str | None = None
    current_agent: str | None = None
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
    agent_name: str | None = None    # run_agent/run_vuln_agent 的 agent enum value（如 "pre-recon"/"recon"/"injection-vuln"）
    phase: str | None = None          # log_phase_* 的 phase label（如 "setup"/"pre-recon"/"recon"/"reporting"）
    info_message: str | None = None   # log_info_activity 用户提示（替代 workflow.logger.info→stderr 抢行）
    info_level: str = "info"          # "info" | "warning"
    vuln_classes: list[str] | None = None   # assemble_report 用（默认 ALL，由 workflow 传 selected）


@dataclass
class PipelineProgress:
    """工作流进度查询返回值。"""
    workflow_id: str
    elapsed_ms: int
    current_phase: str | None
    current_agent: str | None
    completed_agents: list[str]
    status: str
