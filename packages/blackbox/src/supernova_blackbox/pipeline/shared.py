from dataclasses import dataclass, field

from supernova_core.models.base import BasePipelineInput
from supernova_core.constants import DEFAULT_DELIVERABLES_SUBDIR


@dataclass
class BlackboxPipelineInput(BasePipelineInput):
    """Blackbox-specific fields."""
    web_url: str = ""                          # Required for blackbox
    repo_path: str | None = None               # Optional (from whitebox)
    exploit: bool = True
    max_concurrent: int = 3
    retry_profile: str | None = None          # "production" | "testing" | "subscription"
    rerun: bool = False  # 强制重跑黑盒（归档旧 evidence + 新 workflow id）
    correlated_workspace: str | None = None  # 跨仓关联 workspace（B1：复用 topology 做网关层校验，Phase B 接入）
    workspaces_root: str | None = None  # sandbox 外（CLI/worker）解析的 workspaces 根绝对路径（sandbox 内禁 os.getenv/Path.cwd）
    # P3c 阶段 1：provider 配置穿线（Phase C 黑盒 C1 化时由 scan_manager 填；CLI 兜底 None）。
    provider_config: dict | None = None
    # C1 Phase B（黑盒 web 化）：web 提交端塞 events.ndjson 路径（env 不跨容器）。
    # None=CLI 路径（run_scan 外层 wire_web_event_file 注入 env 兜底），对齐 whitebox PipelineInput.event_file。
    event_file: str | None = None


@dataclass
class BlackboxPipelineState:
    status: str = "running"
    current_phase: str | None = None
    current_agent: str | None = None
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    has_whitebox_results: bool = False
    found_whitebox_classes: list[str] = field(default_factory=list)
    start_time: float = 0.0
    errors: list[str] = field(default_factory=list)
    error_code: str | None = None
    failed_agents: list[str] = field(default_factory=list)
    correlation_context: dict | None = None  # B2：关联 workspace topology/boundaries 上下文（供 exploitation 消费）


@dataclass
class BlackboxActivityInput:
    web_url: str
    repo_path: str | None = None
    config_path: str | None = None
    workspace_name: str | None = None
    deliverables_subdir: str = DEFAULT_DELIVERABLES_SUBDIR
    pipeline_testing_mode: bool = False
    api_key: str | None = None
    agent_name: str | None = None
    vuln_type: str | None = None
    workspace_path: str | None = None
    phase: str | None = None          # log_phase_* 的 phase label（如 "preflight"/"recon-blackbox"/"exploitation"/"reporting"）
    correlated_workspace: str | None = None  # 跨仓关联 workspace（B1：由 workflow 从 PipelineInput 透传，Phase B 接入）
    correlation_context: dict | None = None  # B3：关联 workspace topology/boundaries（由 workflow 从 state 注入，exploit activity 消费）
    info_message: str | None = None   # log_info_activity 的用户提示文本（替代裸 logger.warning→stderr 抢行）
    info_level: str = "info"          # "info" | "warning"（rich 着色：cyan/yellow）
    # P3c 阶段 1：provider 配置穿线（Phase C 黑盒 workflow 灌入）。
    provider_config: dict | None = None
    # C1 Phase B：web 路径 setup_display 透传到 AuditSession.initialize→StructuredEventRenderer。
    event_file: str | None = None


@dataclass
class PipelineProgress:
    """工作流进度查询返回值。"""
    workflow_id: str
    elapsed_ms: int
    current_phase: str | None
    current_agent: str | None
    completed_agents: list[str]
    status: str
