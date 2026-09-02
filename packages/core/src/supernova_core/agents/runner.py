from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from supernova_core.models.errors import classify_error_for_temporal

if TYPE_CHECKING:
    from supernova_core.logging.activity_logger import ActivityLogger
    from supernova_core.agents.tool_audit_logger import ToolAuditLogger
    from supernova_core.agents.progress_tool import ProgressSpec
    from supernova_core.collectors.base import CollectorBase


ToolPolicy = Literal["default", "readonly-code"]


@dataclass
class TokenUsage:
    """Token 使用统计信息"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """返回总 token 数量（输入 + 输出）"""
        return self.input_tokens + self.output_tokens


@dataclass
class UsageSink:
    """cancel 中途的已花 usage 出口（2026-08-28 authcheck 超时丢账修复）。

    Temporal start_to_close_timeout 到期以 CancelledError cancel 掉 activity 时，
    run_claude_prompt 正常返回的 usage 拿不到（except Exception 接不住 BaseException）。
    通道：executor.execute 每次创建新实例挂 ``self.usage_sink`` 并下传 provider；
    provider 在被 cancel 前把已累积 usage 写入（openai 引擎 context_wrapper.usage
    每 turn 累积；anthropic CLI 子进程被杀拿不到中途值，保持 0）；activity 的
    cancel 兜底从 executor.usage_sink 读已花值记账。纯数据载体，不算价——
    cost 由 provider cancel 分支用 compute_cost 算好传入。
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    cost_currency: str | None = None
    model: str | None = None

    def record(self, *, model: str | None, input_tokens: int, output_tokens: int,
               cache_read_tokens: int, cache_creation_tokens: int,
               cost_usd: float, cost_currency: str | None) -> None:
        """写入一次部分消耗（provider cancel 分支调用；覆盖语义，非累加）。"""
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = cache_read_tokens
        self.cache_creation_tokens = cache_creation_tokens
        self.cost_usd = cost_usd
        self.cost_currency = cost_currency


@dataclass
class ProviderConfig:
    """AI Provider 配置"""
    type: Literal[
        "anthropic_api",
        "bedrock",
        "vertex",
        "openai_compatible",
        "litellm_router"
    ] = "anthropic_api"
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    region: str | None = None
    project_id: str | None = None
    auth_token: str | None = None
    small_model: str | None = None
    medium_model: str | None = None
    large_model: str | None = None
    # —— P3c 阶段 0：运行时调参，收编引擎内部 os.getenv。
    # 语义：None = 未覆盖（引擎回落 env）；非 None = 显式覆盖（阶段 2 per-ws 配置填充）。
    # 注意：build_provider_config 不主动从 env 读这些字段，只透传显式参数（方案 Y）。——
    max_turns: int | None = None              # CLAUDE_MAX_TURNS / SUPERNOVA_OPENAI_MAX_TURNS
    subagent_max_turns: int | None = None     # SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS
    max_output_tokens: int | None = None      # CLAUDE_CODE_MAX_OUTPUT_TOKENS
    call_timeout: float | None = None         # SUPERNOVA_OPENAI_CALL_TIMEOUT（秒）
    subagent_call_timeout: float | None = None  # SUPERNOVA_OPENAI_SUBAGENT_CALL_TIMEOUT（秒）
    adaptive_thinking: bool | None = None     # CLAUDE_ADAPTIVE_THINKING
    # Per-call workspace pricing override; None preserves process/global layering.
    pricing_override: str | None = None


# 默认模型映射表
DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "anthropic_api": {
        "small": "claude-haiku-4-5-20251001",
        "medium": "claude-sonnet-4-6",
        "large": "claude-opus-4-8",
    },
    "bedrock": {
        "small": "us.anthropic.claude-haiku-4-5",
        "medium": "us.anthropic.claude-sonnet-4-6",
        "large": "us.anthropic.claude-opus-4-8",
    },
    "vertex": {
        "small": "claude-haiku-4-5@latest",
        "medium": "claude-sonnet-4-6@latest",
        "large": "claude-opus-4-8@latest",
    },
    "openai_compatible": {
        "small": "gpt-4o-mini",
        "medium": "gpt-4o",
        "large": "o1",
    },
    "litellm_router": {
        "small": "anthropic/claude-haiku-4-5",
        "medium": "anthropic/claude-sonnet-4-6",
        "large": "anthropic/claude-opus-4-8",
    },
}

@dataclass
class ClaudeRunResult:
    """provider 统一返回类型。字段语义不变量（A2，两引擎 provider 实现义务）：

    - success: 必须真实反映完成/失败。跑满 max_turns / 异常 → False（不得恒 True）。
    - error_code + retryable: 必须分类。成功路径 (None, True)；max_turns →
      ("ExecutionLimitError", False)；限流 → ("RateLimitError", True)；
      超时 → ("TimeoutError", True)；鉴权 → ("AuthenticationError", False)。
      字符串与 models/errors.py:classify_error_for_temporal 对齐。
    - structured_output: 调用方传入 output_format 时，provider 有义务产出非 None。
      优先走原生结构化输出（SDK 的 result_message.structured_output），缺失时从
      final 文本兜底提取 JSON（双引擎一致：anthropic 走 _extract_json_payload，
      openai 走 openai_result_mapper）。
    - cost: best-effort 归集。不支持计费的 provider 允许 0.0（spending-cap 兜底
      不受影响——见 utils/billing.is_spending_cap_behavior 的 cost>0→False 早退）。
    - spending-cap 检测: best-effort，provider 可差异。openai 复用 provider 无关的
      utils/billing.is_spending_cap_behavior，不补 Claude CLI 专属的 dispatcher 层（C2）。
    """
    text: str = ""
    success: bool = False
    duration: int = 0
    turns: int = 0
    cost: float = 0.0
    cost_currency: str = "USD"
    model: str | None = None
    structured_output: Any | None = None
    error: str | None = None
    retryable: bool = True
    error_code: str | None = None
    stop_reason: str | None = None
    tokens: TokenUsage = field(default_factory=TokenUsage)

async def run_claude_prompt(
    prompt: str,
    repo_path: str,
    model_tier: str = "medium",
    output_format: dict | None = None,
    structured_output_schema: dict | None = None,
    api_key: str | None = None,
    deliverables_subdir: str | None = None,
    provider_config: dict | None = None,
    audit_logger: "ActivityLogger | None" = None,
    tool_audit_logger: "ToolAuditLogger | None" = None,
    max_turns: int | None = None,
    collector: "CollectorBase | None" = None,
    progress: "ProgressSpec | None" = None,
    proxy_url: str | None = None,   # Task 4：per-scan 代理穿线 → provider.call
    usage_sink: "UsageSink | None" = None,   # cancel 兜底记账通道（2026-08-28）→ provider.call
    tool_policy: ToolPolicy = "default",
    allowed_roots: list[str | Path] | None = None,
) -> ClaudeRunResult:
    """
    使用 Claude Agent SDK 或兼容 Provider 执行 AI prompt

    Args:
        prompt: 用户提示
        repo_path: 仓库路径（作为工作目录）
        model_tier: 模型层级 (small/medium/large)
        output_format: 结构化输出格式 (JSON Schema)
        structured_output_schema: 结构化输出 schema（别名，与 output_format 相同）
        api_key: API Key（可选，优先级低于 provider_config）
        deliverables_subdir: 产物子目录
        provider_config: Provider 配置字典

    Returns:
        ClaudeRunResult: 执行结果
    """
    # 支持 structured_output_schema 别名
    if output_format is None and structured_output_schema is not None:
        output_format = structured_output_schema

    try:
        # 1. 构建 ProviderConfig
        if provider_config:
            # 使用传入的配置
            config = ProviderConfig(**provider_config)
        else:
            # 从环境变量构建
            from .providers import build_provider_config
            config = build_provider_config(api_key=api_key)

        # 2. 创建 Provider 实例
        from .providers import create_provider
        provider = create_provider(config)

        # L3: adapt the service-layer ActivityLogger into the SDK-domain ToolAuditLogger.
        # Prefer an explicitly-passed tool_audit_logger (e.g. SessionToolAuditLogger
        # wired by run_agent); otherwise wrap audit_logger; otherwise None.
        from .tool_audit_logger import ActivityToolAuditLogger
        if tool_audit_logger is not None:
            active_tool_logger = tool_audit_logger
        elif audit_logger is not None:
            active_tool_logger = ActivityToolAuditLogger(audit_logger)
        else:
            active_tool_logger = None

        # 取消传递通道（spec 2026-08-28-temporal-native-cancel-design）：白盒/黑盒
        # 全部 LLM 调用收敛于此——activity 上下文内周期 heartbeat，web Cancel 后
        # activity 本体经心跳通道秒停（不再跑满超时）。非 activity 上下文（CLI 直调/
        # 单测）no-op，零行为变化。
        from ..runtime.temporal_heartbeat import activity_heartbeat
        async with activity_heartbeat():
            result = await provider.call(
                prompt=prompt,
                cwd=repo_path,
                model_tier=model_tier,
                output_format=output_format,
                deliverables_subdir=deliverables_subdir,
                audit_logger=active_tool_logger,
                max_turns=max_turns,
                collector=collector,
                progress=progress,
                proxy_url=proxy_url,   # Task 4：per-scan 代理穿线 → CLI env / ToolContext
                usage_sink=usage_sink,   # cancel 兜底记账通道：provider cancel 分支写入
                tool_policy=tool_policy,
                allowed_roots=allowed_roots,
            )

        # 5. 检查花费上限行为
        if _is_spending_cap_behavior(result):
            result.success = False
            result.retryable = True
            result.error = result.error or "检测到花费上限限制"
            result.error_code = "BillingError"

        return result

    except Exception as e:
        # 捕获未处理的异常
        error_type, retryable = classify_error_for_temporal(e)
        return ClaudeRunResult(
            text="",
            success=False,
            duration=0,
            turns=0,
            cost=0.0,
            model=None,
            error=f"未处理的异常: {str(e)}",
            retryable=retryable,
            error_code=error_type,
        )


def _is_spending_cap_behavior(result: ClaudeRunResult) -> bool:
    """
    检测结果是否表明花费上限问题

    Args:
        result: Claude 执行结果

    Returns:
        bool: 是否是花费上限问题
    """
    if result.success:
        return False

    if result.error is None:
        return False

    error_lower = result.error.lower()
    keywords = [
        "spending limit",
        "credit limit",
        "quota exceeded",
        "budget exceeded",
        "maximum spend",
    ]

    return any(keyword in error_lower for keyword in keywords)
