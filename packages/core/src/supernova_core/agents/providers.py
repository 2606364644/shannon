"""
Provider 抽象层 - 支持多种 AI Provider

支持的 Provider 类型:
- anthropic_api: Anthropic 官方 API
- bedrock: AWS Bedrock
- vertex: Google Cloud Vertex AI
- openai_compatible: OpenAI 兼容接口
- litellm_router: LiteLLM 路由器
"""

import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from supernova_core.config.provider_settings import PROVIDER_SETTINGS, present

from .runner import ClaudeRunResult, DEFAULT_MODELS, ProviderConfig
from .tool_audit_logger import ToolAuditLogger

if TYPE_CHECKING:
    from supernova_core.collectors.base import CollectorBase


# ============================================================================
# 错误类型定义
# ============================================================================

class ProviderError(Exception):
    """Provider 基础错误类型"""
    pass


class RateLimitError(ProviderError):
    """速率限制错误 - 可重试"""
    pass


class AuthenticationError(ProviderError):
    """认证错误 - 不可重试"""
    pass


class SpendingCapError(ProviderError):
    """花费上限错误 - 可重试（需调整配置）"""
    pass


class TimeoutError(ProviderError):
    """超时错误 - 可重试"""
    pass


class ServiceUnavailableError(ProviderError):
    """服务不可用 - 可重试"""
    pass


# ============================================================================
# Provider 抽象基类
# ============================================================================

class BaseProvider(ABC):
    """AI Provider 抽象基类"""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.type = config.type

    @abstractmethod
    async def call(
        self,
        prompt: str,
        cwd: str,
        model_tier: str = "medium",
        output_format: dict | None = None,
        deliverables_subdir: str | None = None,
        audit_logger: ToolAuditLogger | None = None,
        max_turns: int | None = None,
        collector: "CollectorBase | None" = None,
    ) -> ClaudeRunResult:
        """
        调用 AI 模型执行 prompt

        Args:
            prompt: 用户提示
            cwd: 工作目录
            model_tier: 模型层级 (small/medium/large)
            output_format: 结构化输出格式 (JSON Schema)
            deliverables_subdir: 产物子目录
            audit_logger: provider 无关的逐轮审计日志记录器（可选）
            max_turns: agent 最大轮数（None=引擎默认 200）；>1 启用多轮 agent
            collector: 可选的结构化工具收集器（CollectorBase），注入 set_* 工具
                给 agent（host 渲染产物架构）；provider 各自经 bridge 构造本引擎
                原生工具（claude→MCP server、openai→extra tools）

        Returns:
            ClaudeRunResult: 执行结果（字段语义不变量见 runner.ClaudeRunResult docstring）
        """
        pass

    def _is_retryable_error(self, error: Exception) -> bool:
        """判断错误是否可重试"""
        if isinstance(error, RateLimitError):
            return True
        if isinstance(error, TimeoutError):
            return True
        if isinstance(error, ServiceUnavailableError):
            return True
        if isinstance(error, SpendingCapError):
            return True
        return False


# ============================================================================
# Provider 工厂函数
# ============================================================================

def resolve_tier_model(config: ProviderConfig, model_tier: str) -> str:
    """根据 tier 解析模型名(模块级公共函数, 供两引擎 _get_model 复用 + activity 层用)。

    优先级(对齐两引擎 _get_model): tier-specific config > global config.model > DEFAULT_MODELS。
    provider_key 由 config.type 决定(anthropic_api/bedrock/vertex/openai_compatible/litellm_router)。
    未知 tier -> 回落 medium(DEFAULT_MODELS 兜底, 防 None)。
    """
    # 1. Tier-specific override
    tier_models = {
        "small": config.small_model,
        "medium": config.medium_model,
        "large": config.large_model,
    }
    tier_model = tier_models.get(model_tier)
    if tier_model:
        return tier_model

    # 2. Global model fallback
    if config.model:
        return config.model

    # 3. DEFAULT_MODELS
    ptype = config.type or "anthropic_api"
    if ptype == "bedrock":
        provider_key = "bedrock"
    elif ptype == "vertex":
        provider_key = "vertex"
    elif ptype == "litellm_router":
        provider_key = "litellm_router"
    elif ptype == "openai_compatible":
        provider_key = "openai_compatible"
    else:
        provider_key = "anthropic_api"
    models = DEFAULT_MODELS.get(provider_key, DEFAULT_MODELS["anthropic_api"])
    return models.get(model_tier, models.get("medium", "claude-sonnet-4-6"))


def create_provider(config: ProviderConfig) -> BaseProvider:
    """
    根据配置创建 Provider 实例

    Args:
        config: Provider 配置

    Returns:
        BaseProvider: Provider 实例

    Raises:
        ValueError: 不支持的 Provider 类型
    """
    from .providers_anthropic import AnthropicProvider
    from .providers_openai import OpenAIProvider

    provider_map: dict[str, type[BaseProvider]] = {
        "anthropic_api": AnthropicProvider,
        "bedrock": AnthropicProvider,
        "vertex": AnthropicProvider,
        "openai_compatible": OpenAIProvider,
        "litellm_router": OpenAIProvider,
    }

    provider_class = provider_map.get(config.type)
    if provider_class is None:
        raise ValueError(f"不支持的 Provider 类型: {config.type}")

    return provider_class(config)


def build_provider_config(
    provider_type: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    region: str | None = None,
    project_id: str | None = None,
    auth_token: str | None = None,
    small_model: str | None = None,
    medium_model: str | None = None,
    large_model: str | None = None,
    # —— P3c 阶段 0：运行时调参透传（None=未覆盖，引擎回落 env；build 不读 env）——
    max_turns: int | None = None,
    subagent_max_turns: int | None = None,
    max_output_tokens: int | None = None,
    call_timeout: float | None = None,
    adaptive_thinking: bool | None = None,
) -> ProviderConfig:
    """从环境变量和参数构建 ProviderConfig。

    anthropic_api / openai_compatible: 按 PROVIDER_SETTINGS 直接读取对应前缀变量,
    不做跨前缀 fallback(profile 文件须自洽, profile_validator 启动时兜底校验)。
    bedrock / vertex / litellm_router: 保留现有读取行为(用户未使用, 非本次范围)。

    显式参数优先于环境变量。

    P3c 阶段 0：运行时调参（max_turns 等）只透传，不从 env 读——引擎负责 None 时回落 env。
    """
    if provider_type is None:
        provider_type = os.getenv("SUPERNOVA_AI_PROVIDER", "anthropic_api")

    if provider_type in ("anthropic_api", "openai_compatible"):
        return _build_from_settings(
            provider_type,
            api_key=api_key,
            base_url=base_url,
            model=model,
            auth_token=auth_token,
            small_model=small_model,
            medium_model=medium_model,
            large_model=large_model,
            max_turns=max_turns,
            subagent_max_turns=subagent_max_turns,
            max_output_tokens=max_output_tokens,
            call_timeout=call_timeout,
            adaptive_thinking=adaptive_thinking,
        )

    # bedrock / vertex / litellm_router: 现有 fallback 读取(非本次范围, 保持不变)
    return _build_legacy(
        provider_type,
        api_key=api_key,
        base_url=base_url,
        model=model,
        region=region,
        project_id=project_id,
        auth_token=auth_token,
        small_model=small_model,
        medium_model=medium_model,
        large_model=large_model,
        max_turns=max_turns,
        subagent_max_turns=subagent_max_turns,
        max_output_tokens=max_output_tokens,
        call_timeout=call_timeout,
        adaptive_thinking=adaptive_thinking,
    )


def _read(param: str | None, env_name: str | None) -> str | None:
    """显式参数优先; 否则读环境变量(空串视为未设置); env_name 为 None 时该字段不读。"""
    if param is not None:
        return param
    # 复用 provider_settings.present: set 但空 = unset, 与 profile_validator 语义一致。
    return present(env_name)


def _build_from_settings(
    provider_type: str,
    *,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    auth_token: str | None,
    small_model: str | None,
    medium_model: str | None,
    large_model: str | None,
    max_turns: int | None,
    subagent_max_turns: int | None,
    max_output_tokens: int | None,
    call_timeout: float | None,
    adaptive_thinking: bool | None,
) -> ProviderConfig:
    """anthropic_api / openai_compatible: 按 PROVIDER_SETTINGS 读取, 无跨前缀 fallback。"""
    f = PROVIDER_SETTINGS[provider_type]
    return ProviderConfig(
        type=provider_type,  # type: ignore
        api_key=_read(api_key, f.api_key),
        base_url=_read(base_url, f.base_url),
        model=_read(model, f.model),
        region=None,
        project_id=None,
        auth_token=_read(auth_token, f.auth_token),
        small_model=_read(small_model, f.small_model),
        medium_model=_read(medium_model, f.medium_model),
        large_model=_read(large_model, f.large_model),
        # P3c 阶段 0：透传运行时调参（不读 env；None=引擎回落 env）
        max_turns=max_turns,
        subagent_max_turns=subagent_max_turns,
        max_output_tokens=max_output_tokens,
        call_timeout=call_timeout,
        adaptive_thinking=adaptive_thinking,
    )


def _build_legacy(
    provider_type: str,
    *,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    region: str | None,
    project_id: str | None,
    auth_token: str | None,
    small_model: str | None,
    medium_model: str | None,
    large_model: str | None,
    max_turns: int | None,
    subagent_max_turns: int | None,
    max_output_tokens: int | None,
    call_timeout: float | None,
    adaptive_thinking: bool | None,
) -> ProviderConfig:
    """bedrock / vertex / litellm_router: 保留删除 fallback 前的读取行为。

    openai_compatible 由上游 _build_from_settings 处理, 永不进入本函数;
    因此这里唯一需要走 openai-family(SUPERNOVA_OPENAI_* 优先读)分支的只有 litellm_router。
    """
    is_litellm = provider_type == "litellm_router"

    if api_key is None:
        if is_litellm:
            api_key = os.getenv("SUPERNOVA_OPENAI_API_KEY")
        if api_key is None:
            api_key = (
                os.getenv("SUPERNOVA_API_KEY")
                or os.getenv("ANTHROPIC_API_KEY")
                or os.getenv("OPENAI_API_KEY")
            )
    if base_url is None:
        if is_litellm:
            base_url = os.getenv("SUPERNOVA_OPENAI_BASE_URL")
        if base_url is None:
            base_url = os.getenv("SUPERNOVA_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL")
    if model is None:
        model = os.getenv("SUPERNOVA_MODEL") or os.getenv("ANTHROPIC_MODEL")
    if region is None:
        region = os.getenv("SUPERNOVA_REGION") or os.getenv("AWS_REGION") or os.getenv("CLOUD_ML_REGION")
    if project_id is None:
        project_id = os.getenv("SUPERNOVA_PROJECT_ID") or os.getenv("ANTHROPIC_VERTEX_PROJECT_ID")
    if auth_token is None:
        auth_token = os.getenv("SUPERNOVA_AUTH_TOKEN") or os.getenv("ANTHROPIC_AUTH_TOKEN")
    if small_model is None:
        small_model = (
            os.getenv("SUPERNOVA_OPENAI_SMALL_MODEL") if is_litellm else None
        ) or os.getenv("SUPERNOVA_SMALL_MODEL")
    if medium_model is None:
        medium_model = (
            os.getenv("SUPERNOVA_OPENAI_MEDIUM_MODEL") if is_litellm else None
        ) or os.getenv("SUPERNOVA_MEDIUM_MODEL")
    if large_model is None:
        large_model = (
            os.getenv("SUPERNOVA_OPENAI_LARGE_MODEL") if is_litellm else None
        ) or os.getenv("SUPERNOVA_LARGE_MODEL")

    return ProviderConfig(
        type=provider_type,  # type: ignore
        api_key=api_key,
        base_url=base_url,
        model=model,
        region=region,
        project_id=project_id,
        auth_token=auth_token,
        small_model=small_model,
        medium_model=medium_model,
        large_model=large_model,
        # P3c 阶段 0：透传运行时调参（不读 env；None=引擎回落 env）
        max_turns=max_turns,
        subagent_max_turns=subagent_max_turns,
        max_output_tokens=max_output_tokens,
        call_timeout=call_timeout,
        adaptive_thinking=adaptive_thinking,
    )
