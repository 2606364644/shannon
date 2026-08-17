from dataclasses import dataclass, field

from supernova_core.models.base import BasePipelineInput
from supernova_core.constants import DEFAULT_DELIVERABLES_SUBDIR

# LLM 引擎级错误的消息签名（无 provider_error_code context 的旧路径/直抛异常兜底）。
# openai SDK 标准格式 "Error code: 40x - {...}" + 常见 provider 限额/令牌文案。
_ENGINE_ERROR_MARKERS: tuple[str, ...] = (
    "error code: 401", "error code: 403", "error code: 429",
    "令牌已过期", "invalid api key", "incorrect api key",
    "rate limit", "ratelimit", "quota", "insufficient credit",
    "exceeded your current quota",
)
_ENGINE_ERROR_TYPE_NAMES = frozenset({
    "authenticationerror", "permissionerror", "ratelimiterror", "permissiondeniederror",
})


def is_engine_failure(exc: BaseException) -> bool:
    """LLM 引擎级失败判定：驱动 agent 的 provider 调用自身失败，与目标站登录无关。

    两级判据：① executor 已把 provider 语义错误类（AuthenticationError 等）塞进
    PentestError.context["provider_error_code"]；② 消息/类型签名兜底（openai SDK
    "Error code: 4xx"、智谱「令牌已过期」、限额类文案）。命中 → auth 探针记
    failure_point="engine"，前端提示用户查 LLM 配置而非账号密码。

    放 shared.py（非 activities.py）：workflow 沙箱可安全导入的纯函数，
    batch workflow 的框架级兜底 except 复用同一判据。"""
    ctx = getattr(exc, "context", None)
    if isinstance(ctx, dict) and ctx.get("provider_error_code"):
        return True
    if type(exc).__name__.lower() in _ENGINE_ERROR_TYPE_NAMES:
        return True
    msg = str(exc).lower()
    return any(marker in msg for marker in _ENGINE_ERROR_MARKERS)


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
    # Phase 2（HOST 档案）：host→IP 映射（per-scan 代理消费），web 层 scan_manager 填。
    # 空=未启用 HOST 档案；T7/T8 透传到 activity / sandbox /etc/hosts 注入。
    host_mappings: dict[str, str] = field(default_factory=dict)


@dataclass
class BlackboxAuthValidationInput(BasePipelineInput):
    """AuthValidationWorkflow 入参(独立认证验证探针,非扫描流程)。

    仅承载探针所需:web_url(=login_url)+ config_path(probe scan-config.yaml)+
    workspace_path(probe 目录,auth-state.json 落点)+ api_key。不跑扫描其余步骤。
    字段需有默认值(BasePipelineInput 字段均有默认,dataclass 不允许 default 后非 default)。
    """
    web_url: str = ""
    workspace_path: str | None = None
    # 块1（认证验证可观测性）：probe events.ndjson 落点，透传 setup_display 写 agent 登录过程。
    # None=未启用可观测性（CLI 直调等），setup_display 拿到 None 不挂 StructuredEventRenderer。
    event_file: str | None = None
    # Combined auth precheck may run in its own workflow, but it must receive the
    # same immutable HOST snapshot as the subsequent blackbox workflow.
    host_mappings: dict[str, str] = field(default_factory=dict)
    # 完整 provider 配置穿线（对齐 BlackboxPipelineInput P3c 阶段 1：base_url+key+模型一体）。
    # 仅传 api_key 会让 base_url/模型回落 worker env profile——key 与端点来自两套配置时
    # 必然 401（2026-08-17 NodeGoat 探针根因）。None=CLI/env 兜底路径，行为不变。
    provider_config: dict | None = None


@dataclass
class BlackboxAuthValidationBatchItem:
    """BatchAuthValidationWorkflow 单 cred 项（逐个独立验证一个角色能否登录，非越权对比）。

    cred_id 是 web 层概念（回填 verify_status 的 key），透传到 workflow 供 batch_progress
    query 返回 per-cred 进度；web_url/config_path/workspace_path/event_file 同单 cred 探针。
    role 不入此结构（认证测试的 role 仅前端展示元数据，不影响 core 登录链路，spec §2）。

    host_mappings（2026-08-14）：认证测试复用黑盒 HOST 档案能力——选中 HOST 才走代理、不选直连。
    对齐单 cred BlackboxAuthValidationInput.host_mappings：空=直连，非空=起 per-cred host proxy。
    """
    cred_id: str
    web_url: str = ""
    config_path: str | None = None
    workspace_path: str | None = None
    event_file: str | None = None
    host_mappings: dict[str, str] = field(default_factory=dict)


@dataclass
class BlackboxAuthValidationBatchInput(BasePipelineInput):
    """BatchAuthValidationWorkflow 入参：档案级多选角色 → 串行逐个独立验证（spec §4.3）。

    api_key 在 profile 级共享（同 provider）；items 各自独立 probe/events/workspace_path。
    workflow 串行 for item（同时只一个 cred running，与 web 层 per-cred running 恢复契合）。
    """
    items: list[BlackboxAuthValidationBatchItem] = field(default_factory=list)
    api_key: str | None = None
    # 同 BlackboxAuthValidationInput.provider_config：profile 级共享（同 provider），
    # items 各自独立 probe/events/workspace_path。
    provider_config: dict | None = None


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
    # Phase 2（HOST 档案）：per-activity 的 host→IP 映射 + 上游代理 URL。
    # 由 workflow 从 BlackboxPipelineInput.host_mappings 透传；proxy_url 来自 scan_manager。
    host_mappings: dict[str, str] = field(default_factory=dict)
    proxy_url: str | None = None
    # 扫描期 per-workspace env 覆盖（scan_env 覆盖层用）；由 workflow 从 BlackboxPipelineInput 灌入。
    env_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class PipelineProgress:
    """工作流进度查询返回值。"""
    workflow_id: str
    elapsed_ms: int
    current_phase: str | None
    current_agent: str | None
    completed_agents: list[str]
    status: str
