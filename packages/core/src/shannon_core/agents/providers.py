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

from shannon_core.config.provider_settings import PROVIDER_SETTINGS

from .runner import ClaudeRunResult, ProviderConfig
from .tool_audit_logger import ToolAuditLogger


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

        Returns:
            ClaudeRunResult: 执行结果
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
) -> ProviderConfig:
    """从环境变量和参数构建 ProviderConfig。

    anthropic_api / openai_compatible: 按 PROVIDER_SETTINGS 直接读取对应前缀变量,
    不做跨前缀 fallback(profile 文件须自洽, profile_validator 启动时兜底校验)。
    bedrock / vertex / litellm_router: 保留现有读取行为(用户未使用, 非本次范围)。

    显式参数优先于环境变量。
    """
    if provider_type is None:
        provider_type = os.getenv("SHANNON_AI_PROVIDER", "anthropic_api")

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
    )


def _read(param: str | None, env_name: str | None) -> str | None:
    """显式参数优先; 否则读环境变量; env_name 为 None 时该字段不读。"""
    if param is not None:
        return param
    if env_name is None:
        return None
    return os.getenv(env_name)


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
) -> ProviderConfig:
    """bedrock / vertex / litellm_router: 保留删除 fallback 前的读取行为。"""
    is_openai_family = provider_type in ("openai_compatible", "litellm_router")

    if api_key is None:
        if is_openai_family:
            api_key = os.getenv("SHANNON_OPENAI_API_KEY")
        if api_key is None:
            api_key = (
                os.getenv("SHANNON_API_KEY")
                or os.getenv("ANTHROPIC_API_KEY")
                or os.getenv("OPENAI_API_KEY")
            )
    if base_url is None:
        if is_openai_family:
            base_url = os.getenv("SHANNON_OPENAI_BASE_URL")
        if base_url is None:
            base_url = os.getenv("SHANNON_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL")
    if model is None:
        model = os.getenv("SHANNON_MODEL") or os.getenv("ANTHROPIC_MODEL")
    if region is None:
        region = os.getenv("SHANNON_REGION") or os.getenv("AWS_REGION") or os.getenv("CLOUD_ML_REGION")
    if project_id is None:
        project_id = os.getenv("SHANNON_PROJECT_ID") or os.getenv("ANTHROPIC_VERTEX_PROJECT_ID")
    if auth_token is None:
        auth_token = os.getenv("SHANNON_AUTH_TOKEN") or os.getenv("ANTHROPIC_AUTH_TOKEN")
    if small_model is None:
        small_model = (
            os.getenv("SHANNON_OPENAI_SMALL_MODEL") if is_openai_family else None
        ) or os.getenv("SHANNON_SMALL_MODEL")
    if medium_model is None:
        medium_model = (
            os.getenv("SHANNON_OPENAI_MEDIUM_MODEL") if is_openai_family else None
        ) or os.getenv("SHANNON_MEDIUM_MODEL")
    if large_model is None:
        large_model = (
            os.getenv("SHANNON_OPENAI_LARGE_MODEL") if is_openai_family else None
        ) or os.getenv("SHANNON_LARGE_MODEL")

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
    )
