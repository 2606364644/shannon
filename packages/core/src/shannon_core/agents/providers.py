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

from .runner import ClaudeRunResult, ProviderConfig


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
    ) -> ClaudeRunResult:
        """
        调用 AI 模型执行 prompt

        Args:
            prompt: 用户提示
            cwd: 工作目录
            model_tier: 模型层级 (small/medium/large)
            output_format: 结构化输出格式 (JSON Schema)
            deliverables_subdir: 产物子目录

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
) -> ProviderConfig:
    """
    从环境变量和参数构建 ProviderConfig

    零配置用法: 只需设置 ANTHROPIC_API_KEY 环境变量即可。
    SHANNON_* 变量用于覆盖默认行为。

    环境变量优先级: 参数 > SHANNON_* > ANTHROPIC_*

    Args:
        provider_type: Provider 类型（默认 anthropic_api）
        api_key: API Key（默认从 SHANNON_API_KEY > ANTHROPIC_API_KEY 读取）
        base_url: Base URL（默认从 SHANNON_BASE_URL > ANTHROPIC_BASE_URL 读取）
        model: 模型名称（默认从 SHANNON_MODEL > ANTHROPIC_MODEL 读取）
        region: 区域（用于 Bedrock / Vertex）
        project_id: 项目 ID（用于 Vertex）
        auth_token: 认证 Token（用于 LiteLLM）

    Returns:
        ProviderConfig: 配置对象
    """
    # Provider 类型
    if provider_type is None:
        provider_type = os.getenv("SHANNON_AI_PROVIDER", "anthropic_api")

    # API Key - 优先 SHANNON_API_KEY，其次 ANTHROPIC_API_KEY
    if api_key is None:
        api_key = os.getenv("SHANNON_API_KEY") or os.getenv("ANTHROPIC_API_KEY")

    # Base URL - 优先 SHANNON_BASE_URL，其次 ANTHROPIC_BASE_URL
    if base_url is None:
        base_url = os.getenv("SHANNON_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL")

    # Model - 优先 SHANNON_MODEL，其次 ANTHROPIC_MODEL
    if model is None:
        model = os.getenv("SHANNON_MODEL") or os.getenv("ANTHROPIC_MODEL")

    # Region - 用于 Bedrock 和 Vertex
    if region is None:
        region = os.getenv("SHANNON_REGION") or os.getenv("AWS_REGION") or os.getenv("CLOUD_ML_REGION")

    # Project ID - 用于 Vertex
    if project_id is None:
        project_id = os.getenv("SHANNON_PROJECT_ID") or os.getenv("ANTHROPIC_VERTEX_PROJECT_ID")

    # Auth Token - 用于 LiteLLM
    if auth_token is None:
        auth_token = os.getenv("SHANNON_AUTH_TOKEN") or os.getenv("ANTHROPIC_AUTH_TOKEN")

    return ProviderConfig(
        type=provider_type,  # type: ignore
        api_key=api_key,
        base_url=base_url,
        model=model,
        region=region,
        project_id=project_id,
        auth_token=auth_token,
    )
