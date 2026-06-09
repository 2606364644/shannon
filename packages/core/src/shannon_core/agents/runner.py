from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from shannon_core.models.errors import classify_error_for_temporal

if TYPE_CHECKING:
    from shannon_core.logging.activity_logger import ActivityLogger


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
    text: str = ""
    success: bool = False
    duration: int = 0
    turns: int = 0
    cost: float = 0.0
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

        # L3: adapt the service-layer ActivityLogger into the SDK-domain ToolAuditLogger
        from .tool_audit_logger import ActivityToolAuditLogger
        tool_audit_logger = ActivityToolAuditLogger(audit_logger) if audit_logger is not None else None

        result = await provider.call(
            prompt=prompt,
            cwd=repo_path,
            model_tier=model_tier,
            output_format=output_format,
            deliverables_subdir=deliverables_subdir,
            audit_logger=tool_audit_logger,
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
