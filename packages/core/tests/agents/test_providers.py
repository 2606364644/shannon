"""
测试 Provider 抽象层
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shannon_core.agents.providers import (
    AuthenticationError,
    ProviderError,
    RateLimitError,
    SpendingCapError,
    build_provider_config,
    create_provider,
)
from shannon_core.agents.providers_anthropic import AnthropicProvider
from shannon_core.agents.providers_openai import OpenAIProvider
from shannon_core.agents.runner import ClaudeRunResult, ProviderConfig, TokenUsage


class TestProviderConfig:
    """测试 ProviderConfig 数据类"""

    def test_default_config(self):
        """测试默认配置"""
        config = ProviderConfig()
        assert config.type == "anthropic_api"
        assert config.api_key is None
        assert config.base_url is None

    def test_full_config(self):
        """测试完整配置"""
        config = ProviderConfig(
            type="openai_compatible",
            api_key="test-key",
            base_url="https://api.example.com",
            model="gpt-4o",
        )
        assert config.type == "openai_compatible"
        assert config.api_key == "test-key"
        assert config.base_url == "https://api.example.com"


class TestTokenUsage:
    """测试 TokenUsage 数据类"""

    def test_default_values(self):
        """测试默认值"""
        tokens = TokenUsage()
        assert tokens.input_tokens == 0
        assert tokens.output_tokens == 0
        assert tokens.cache_creation_input_tokens == 0
        assert tokens.cache_read_input_tokens == 0

    def test_total_tokens(self):
        """测试总 token 计算"""
        tokens = TokenUsage(input_tokens=1000, output_tokens=500)
        assert tokens.total_tokens == 1500

    def test_with_cache(self):
        """测试包含缓存的 token 统计"""
        tokens = TokenUsage(
            input_tokens=1000,
            output_tokens=500,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=300,
        )
        assert tokens.input_tokens == 1000
        assert tokens.output_tokens == 500
        assert tokens.cache_creation_input_tokens == 200
        assert tokens.cache_read_input_tokens == 300


class TestBuildProviderConfig:
    """测试 build_provider_config 函数"""

    def test_default_provider(self):
        """测试默认 Provider 类型"""
        config = build_provider_config()
        assert config.type == "anthropic_api"

    def test_shannon_env_vars(self):
        """测试 SHANNON_* 环境变量"""
        with patch.dict(os.environ, {
            "SHANNON_AI_PROVIDER": "openai_compatible",
            "SHANNON_API_KEY": "test-key",
            "SHANNON_BASE_URL": "https://api.example.com",
            "SHANNON_MODEL": "gpt-4o",
        }):
            config = build_provider_config()
            assert config.type == "openai_compatible"
            assert config.api_key == "test-key"
            assert config.base_url == "https://api.example.com"
            assert config.model == "gpt-4o"

    def test_anthropic_env_vars_fallback(self):
        """测试 ANTHROPIC_* 环境变量回退"""
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "ANTHROPIC_BASE_URL": "https://anthropic.example.com",
        }, clear=True):
            config = build_provider_config()
            assert config.api_key == "anthropic-key"
            assert config.base_url == "https://anthropic.example.com"

    def test_shannon_priority_over_anthropic(self):
        """测试 SHANNON_* 优先级高于 ANTHROPIC_*"""
        with patch.dict(os.environ, {
            "SHANNON_API_KEY": "shannon-key",
            "ANTHROPIC_API_KEY": "anthropic-key",
        }):
            config = build_provider_config()
            assert config.api_key == "shannon-key"

    def test_explicit_params_override_env(self):
        """测试显式参数覆盖环境变量"""
        with patch.dict(os.environ, {
            "SHANNON_API_KEY": "env-key",
        }):
            config = build_provider_config(api_key="param-key")
            assert config.api_key == "param-key"

    def test_bedrock_config(self):
        """测试 Bedrock 配置"""
        with patch.dict(os.environ, {
            "SHANNON_AI_PROVIDER": "bedrock",
            "AWS_REGION": "us-west-2",
        }):
            config = build_provider_config()
            assert config.type == "bedrock"
            assert config.region == "us-west-2"

    def test_vertex_config(self):
        """测试 Vertex 配置"""
        with patch.dict(os.environ, {
            "SHANNON_AI_PROVIDER": "vertex",
            "SHANNON_PROJECT_ID": "test-project",
            "CLOUD_ML_REGION": "us-central1",
        }):
            config = build_provider_config()
            assert config.type == "vertex"
            assert config.project_id == "test-project"
            assert config.region == "us-central1"


class TestCreateProvider:
    """测试 create_provider 工厂函数"""

    def test_create_anthropic_provider(self):
        """测试创建 Anthropic Provider"""
        config = ProviderConfig(type="anthropic_api")
        provider = create_provider(config)
        assert isinstance(provider, AnthropicProvider)
        assert provider.type == "anthropic_api"

    def test_create_bedrock_provider(self):
        """测试创建 Bedrock Provider"""
        config = ProviderConfig(type="bedrock")
        provider = create_provider(config)
        assert isinstance(provider, AnthropicProvider)
        assert provider.type == "bedrock"

    def test_create_vertex_provider(self):
        """测试创建 Vertex Provider"""
        config = ProviderConfig(type="vertex")
        provider = create_provider(config)
        assert isinstance(provider, AnthropicProvider)
        assert provider.type == "vertex"

    def test_create_openai_provider(self):
        """测试创建 OpenAI Provider"""
        config = ProviderConfig(type="openai_compatible")
        provider = create_provider(config)
        assert isinstance(provider, OpenAIProvider)
        assert provider.type == "openai_compatible"

    def test_create_litellm_provider(self):
        """测试创建 LiteLLM Provider"""
        config = ProviderConfig(type="litellm_router")
        provider = create_provider(config)
        assert isinstance(provider, OpenAIProvider)
        assert provider.type == "litellm_router"

    def test_unsupported_provider(self):
        """测试不支持的 Provider 类型"""
        config = ProviderConfig(type="unsupported")
        with pytest.raises(ValueError, match="不支持的 Provider 类型"):
            create_provider(config)


class TestAnthropicProvider:
    """测试 AnthropicProvider"""

    def test_get_model_default(self):
        """测试获取默认模型"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        assert provider._get_model("medium") == "claude-sonnet-4-6"

    def test_get_model_explicit(self):
        """测试显式指定的模型"""
        config = ProviderConfig(type="anthropic_api", model="claude-opus-4-8")
        provider = AnthropicProvider(config)
        assert provider._get_model("medium") == "claude-opus-4-8"

    def test_get_model_bedrock(self):
        """测试 Bedrock 模型选择"""
        config = ProviderConfig(type="bedrock")
        provider = AnthropicProvider(config)
        assert provider._get_model("medium") == "us.anthropic.claude-sonnet-4-6"

    def test_is_adaptive_thinking_enabled(self):
        """测试 adaptive thinking 检测"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"CLAUDE_ADAPTIVE_THINKING": "true"}):
            assert provider._is_adaptive_thinking_enabled() is True

        with patch.dict(os.environ, {"CLAUDE_ADAPTIVE_THINKING": "false"}):
            assert provider._is_adaptive_thinking_enabled() is False

    def test_is_spending_cap_error(self):
        """测试花费上限错误检测"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        assert provider._is_spending_cap_error("spending limit reached") is True
        assert provider._is_spending_cap_error("credit limit exceeded") is True
        assert provider._is_spending_cap_error("quota exceeded") is True
        assert provider._is_spending_cap_error("normal error") is False

    @pytest.mark.asyncio
    async def test_call_success(self):
        """测试成功调用"""
        from claude_agent_sdk import ResultMessage

        config = ProviderConfig(type="anthropic_api", api_key="test-key")
        provider = AnthropicProvider(config)

        # 创建真实的 ResultMessage
        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50
        mock_usage.cache_creation_input_tokens = 10
        mock_usage.cache_read_input_tokens = 5

        mock_result = ResultMessage(
            subtype="result",
            duration_ms=1000,
            duration_api_ms=500,
            is_error=False,
            num_turns=1,
            session_id="test-session",
            total_cost_usd=0.001,
            usage=mock_usage,
            result="Test response",
        )
        mock_result.collected_text = "Test response"

        # Mock query 函数
        async def mock_query(*, prompt, options):
            yield mock_result

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider.call(
                prompt="Test prompt",
                cwd="/tmp",
                model_tier="medium",
            )

        assert result.success is True
        assert result.text == "Test response"
        assert result.tokens.input_tokens == 100
        assert result.tokens.output_tokens == 50
        assert result.cost == 0.001


class TestOpenAIProvider:
    """测试 OpenAIProvider"""

    def test_get_model_default(self):
        """测试获取默认模型"""
        config = ProviderConfig(type="openai_compatible")
        provider = OpenAIProvider(config)
        assert provider._get_model("medium") == "gpt-4o"

    def test_get_model_explicit(self):
        """测试显式指定的模型"""
        config = ProviderConfig(type="openai_compatible", model="gpt-4o-mini")
        provider = OpenAIProvider(config)
        assert provider._get_model("medium") == "gpt-4o-mini"

    def test_estimate_cost(self):
        """测试成本估算"""
        config = ProviderConfig(type="openai_compatible")
        provider = OpenAIProvider(config)

        tokens = TokenUsage(input_tokens=1000, output_tokens=500)
        cost = provider._estimate_cost("gpt-4o-mini", tokens)

        # gpt-4o-mini: input $0.00015/1K, output $0.0006/1K
        # (1000 * 0.00015 / 1000) + (500 * 0.0006 / 1000)
        # = 0.00015 + 0.0003 = 0.00045
        assert abs(cost - 0.00045) < 0.00001

    def test_is_retryable_error(self):
        """测试错误分类"""
        config = ProviderConfig(type="openai_compatible")
        provider = OpenAIProvider(config)

        # 可重试错误
        assert provider._is_retryable_error(Exception("rate limit exceeded")) is True
        assert provider._is_retryable_error(Exception("timeout")) is True

        # 不可重试错误
        assert provider._is_retryable_error(Exception("authentication failed")) is False
        assert provider._is_retryable_error(Exception("permission denied")) is False


class TestClaudeRunResult:
    """测试 ClaudeRunResult"""

    def test_default_result(self):
        """测试默认结果"""
        result = ClaudeRunResult()
        assert result.success is False
        assert result.retryable is True
        assert result.tokens.input_tokens == 0

    def test_result_with_tokens(self):
        """测试包含 token 统计的结果"""
        tokens = TokenUsage(input_tokens=100, output_tokens=50)
        result = ClaudeRunResult(
            text="Test",
            success=True,
            tokens=tokens,
        )
        assert result.tokens.input_tokens == 100
        assert result.tokens.output_tokens == 50
