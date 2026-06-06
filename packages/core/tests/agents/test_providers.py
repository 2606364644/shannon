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
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage
from shannon_core.agents.providers_anthropic import AnthropicProvider
from shannon_core.agents.message_dispatcher import MessageDispatcher
from shannon_core.agents.providers_openai import OpenAIProvider
from shannon_core.agents.runner import ClaudeRunResult, ProviderConfig, TokenUsage, run_claude_prompt


class TestProviderConfig:
    """测试 ProviderConfig 数据类"""

    def test_default_config(self):
        """测试默认配置"""
        config = ProviderConfig()
        assert config.type == "anthropic_api"
        assert config.api_key is None
        assert config.base_url is None

    def test_tier_specific_model_fields_default_to_none(self):
        """Tier-specific model fields default to None"""
        config = ProviderConfig()
        assert config.small_model is None
        assert config.medium_model is None
        assert config.large_model is None

    def test_tier_specific_model_fields_can_be_set(self):
        """Tier-specific model fields can be explicitly set"""
        config = ProviderConfig(
            small_model="claude-haiku-4-5-20251001",
            medium_model="claude-sonnet-4-6",
            large_model="claude-opus-4-8",
        )
        assert config.small_model == "claude-haiku-4-5-20251001"
        assert config.medium_model == "claude-sonnet-4-6"
        assert config.large_model == "claude-opus-4-8"

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

    def test_tier_specific_env_vars(self):
        """测试 SHANNON_*_MODEL 环境变量"""
        with patch.dict(os.environ, {
            "SHANNON_SMALL_MODEL": "custom-small",
            "SHANNON_MEDIUM_MODEL": "custom-medium",
            "SHANNON_LARGE_MODEL": "custom-large",
        }):
            config = build_provider_config()
            assert config.small_model == "custom-small"
            assert config.medium_model == "custom-medium"
            assert config.large_model == "custom-large"

    def test_tier_specific_env_vars_partial(self):
        """测试只设置部分 tier 变量"""
        with patch.dict(os.environ, {
            "SHANNON_MEDIUM_MODEL": "custom-medium",
        }):
            config = build_provider_config()
            assert config.small_model is None
            assert config.medium_model == "custom-medium"
            assert config.large_model is None

    def test_tier_specific_env_vars_default_to_none(self):
        """测试不设置 tier 变量时默认为 None"""
        with patch.dict(os.environ, {}, clear=True):
            config = build_provider_config()
            assert config.small_model is None
            assert config.medium_model is None
            assert config.large_model is None

    def test_tier_specific_params_override_env(self):
        """测试显式参数覆盖 tier 环境变量"""
        with patch.dict(os.environ, {
            "SHANNON_MEDIUM_MODEL": "env-medium",
        }):
            config = build_provider_config(medium_model="param-medium")
            assert config.medium_model == "param-medium"


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

        # Mock query 函数 — include text event so dispatcher collects it
        async def mock_query(*, prompt, options):
            text_event = MagicMock()
            text_event.type = "text"
            text_event.text = "Test response"
            yield text_event
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


class TestAnthropicProviderBuildOptions:
    """测试 AnthropicProvider._build_options 的零配置行为"""

    def test_no_env_override_with_anthropic_key_only(self):
        """当只有 ANTHROPIC_API_KEY 时，options.env 应包含从进程继承的 key（SDK 不再自动读取）"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True):
            options = provider._build_options(
                cwd="/tmp",
                model="claude-sonnet-4-6",
            )

        assert options.env is not None
        assert options.env.get("ANTHROPIC_API_KEY") == "sk-ant-test"

    def test_env_override_with_shannon_api_key(self):
        """当 config.api_key 设置时，应传入 options.env"""
        config = ProviderConfig(type="anthropic_api", api_key="config-key")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {}, clear=True):
            options = provider._build_options(
                cwd="/tmp",
                model="claude-sonnet-4-6",
            )

        assert options.env is not None
        assert options.env["ANTHROPIC_API_KEY"] == "config-key"

    def test_env_override_with_shannon_base_url(self):
        """当 ANTHROPIC_BASE_URL 在进程环境中时，应传入 options.env"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://custom.example.com"}, clear=True):
            options = provider._build_options(
                cwd="/tmp",
                model="claude-sonnet-4-6",
            )

        assert options.env is not None
        assert options.env["ANTHROPIC_BASE_URL"] == "https://custom.example.com"

    def test_both_shannon_overrides(self):
        """当 config.api_key 和 ANTHROPIC_BASE_URL 同时设置时"""
        config = ProviderConfig(type="anthropic_api", api_key="config-key")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://custom.example.com"}, clear=True):
            options = provider._build_options(
                cwd="/tmp",
                model="claude-sonnet-4-6",
            )

        assert options.env is not None
        assert options.env["ANTHROPIC_API_KEY"] == "config-key"
        assert options.env["ANTHROPIC_BASE_URL"] == "https://custom.example.com"

    def test_bedrock_env_still_set(self):
        """Bedrock provider 仍应设置 options.env（不受改动影响）"""
        config = ProviderConfig(type="bedrock", region="us-west-2")
        provider = AnthropicProvider(config)

        options = provider._build_options(
            cwd="/tmp",
            model="us.anthropic.claude-sonnet-4-6",
        )

        assert options.env is not None
        assert options.env["AWS_REGION"] == "us-west-2"

    def test_vertex_env_still_set(self):
        """Vertex provider 仍应设置 options.env（不受改动影响）"""
        config = ProviderConfig(
            type="vertex",
            region="us-central1",
            project_id="test-project",
        )
        provider = AnthropicProvider(config)

        options = provider._build_options(
            cwd="/tmp",
            model="claude-sonnet-4-6@latest",
        )

        assert options.env is not None
        assert options.env["CLOUD_ML_REGION"] == "us-central1"
        assert options.env["ANTHROPIC_VERTEX_PROJECT_ID"] == "test-project"


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

    def test_result_with_error_code(self):
        """测试 error_code 字段"""
        result = ClaudeRunResult(
            text="",
            success=False,
            error="authentication failed",
            error_code="AuthenticationError",
        )
        assert result.error_code == "AuthenticationError"

    def test_error_code_defaults_to_none(self):
        """测试 error_code 默认为 None"""
        result = ClaudeRunResult()
        assert result.error_code is None


class TestBuildSdkEnv:
    """Test AnthropicProvider._build_sdk_env() env var passthrough."""

    def test_anthropic_api_with_config_api_key(self):
        """Config api_key is forwarded as ANTHROPIC_API_KEY."""
        config = ProviderConfig(type="anthropic_api", api_key="cfg-key")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {}, clear=True):
            env = provider._build_sdk_env()

        assert env.get("ANTHROPIC_API_KEY") == "cfg-key"

    def test_anthropic_api_passthrough_from_process_env(self):
        """Without config override, inherits ANTHROPIC_API_KEY from process env."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "env-key"}, clear=True):
            env = provider._build_sdk_env()

        assert env.get("ANTHROPIC_API_KEY") == "env-key"

    def test_anthropic_api_config_overrides_env(self):
        """Config api_key takes precedence over process env ANTHROPIC_API_KEY."""
        config = ProviderConfig(type="anthropic_api", api_key="cfg-key")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "env-key"}, clear=True):
            env = provider._build_sdk_env()

        assert env["ANTHROPIC_API_KEY"] == "cfg-key"

    def test_bedrock_sets_flags(self):
        """Bedrock provider sets CLAUDE_CODE_USE_BEDROCK and AWS_REGION."""
        config = ProviderConfig(type="bedrock", region="us-west-2")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {}, clear=True):
            env = provider._build_sdk_env()

        assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
        assert env["AWS_REGION"] == "us-west-2"

    def test_vertex_sets_flags(self):
        """Vertex provider sets CLAUDE_CODE_USE_VERTEX, CLOUD_ML_REGION, ANTHROPIC_VERTEX_PROJECT_ID."""
        config = ProviderConfig(type="vertex", region="europe-west1", project_id="proj-123")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {}, clear=True):
            env = provider._build_sdk_env()

        assert env["CLAUDE_CODE_USE_VERTEX"] == "1"
        assert env["CLOUD_ML_REGION"] == "europe-west1"
        assert env["ANTHROPIC_VERTEX_PROJECT_ID"] == "proj-123"

    def test_litellm_router_sets_base_url_and_auth_token(self):
        """LiteLLM router forwards base_url and auth_token."""
        config = ProviderConfig(
            type="litellm_router",
            base_url="https://router.example.com",
            auth_token="tok-abc",
        )
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {}, clear=True):
            env = provider._build_sdk_env()

        assert env["ANTHROPIC_BASE_URL"] == "https://router.example.com"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "tok-abc"

    def test_passthrough_inherits_home_and_path(self):
        """HOME and PATH are always inherited from process env."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"HOME": "/home/test", "PATH": "/usr/bin"}, clear=True):
            env = provider._build_sdk_env()

        assert env["HOME"] == "/home/test"
        assert env["PATH"] == "/usr/bin"

    def test_passthrough_inherits_oauth_token(self):
        """CLAUDE_CODE_OAUTH_TOKEN is inherited from process env."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "oauth-tok"}, clear=True):
            env = provider._build_sdk_env()

        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-tok"

    def test_passthrough_inherits_playwright_path(self):
        """PLAYWRIGHT_MCP_EXECUTABLE_PATH is inherited from process env."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"PLAYWRIGHT_MCP_EXECUTABLE_PATH": "/usr/local/bin/npx"}, clear=True):
            env = provider._build_sdk_env()

        assert env["PLAYWRIGHT_MCP_EXECUTABLE_PATH"] == "/usr/local/bin/npx"

    def test_max_output_tokens_forwarded(self):
        """CLAUDE_CODE_MAX_OUTPUT_TOKENS is forwarded when set."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "128000"}, clear=True):
            env = provider._build_sdk_env()

        assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "128000"

    def test_default_max_output_tokens(self):
        """CLAUDE_CODE_MAX_OUTPUT_TOKENS defaults to 64000."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {}, clear=True):
            env = provider._build_sdk_env()

        assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "64000"

    def test_bedrock_inherits_bearer_token(self):
        """Bedrock inherits AWS_BEARER_TOKEN_BEDROCK from process env."""
        config = ProviderConfig(type="bedrock", region="us-east-1")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "bearer-tok"}, clear=True):
            env = provider._build_sdk_env()

        assert env["AWS_BEARER_TOKEN_BEDROCK"] == "bearer-tok"

    def test_vertex_inherits_google_credentials(self):
        """Vertex inherits GOOGLE_APPLICATION_CREDENTIALS from process env."""
        config = ProviderConfig(type="vertex", region="us-central1", project_id="proj")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": "/path/to/creds.json"}, clear=True):
            env = provider._build_sdk_env()

        assert env["GOOGLE_APPLICATION_CREDENTIALS"] == "/path/to/creds.json"

    def test_no_empty_values(self):
        """No empty-string values appear in the result."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        with patch.dict(os.environ, {}, clear=True):
            env = provider._build_sdk_env()

        for key, val in env.items():
            assert val != "", f"Empty value for {key}"


class TestExecuteQueryWithDispatcher:
    """Test _execute_query uses MessageDispatcher for event processing."""

    @pytest.mark.asyncio
    async def test_dispatcher_collects_text_from_events(self):
        """_execute_query collects text via dispatcher from mixed events."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        text_event = MagicMock()
        text_event.type = "text"
        text_event.text = "partial "

        assistant_event = MagicMock()
        assistant_event.type = "assistant"
        block = MagicMock()
        block.text = "response"
        assistant_event.content = [block]
        assistant_event.error = None

        mock_result = ResultMessage(
            subtype="result",
            duration_ms=1000,
            duration_api_ms=500,
            is_error=False,
            num_turns=1,
            session_id="test",
        )

        events = [text_event, assistant_event, mock_result]

        async def mock_query(*, prompt, options):
            for e in events:
                yield e

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider._execute_query(
                prompt="test",
                options=ClaudeAgentOptions(model="claude-sonnet-4-6", cwd="/tmp"),
            )

        assert result.collected_text == "partial response"
        assert result.turn_count == 1

    @pytest.mark.asyncio
    async def test_dispatcher_with_custom_logger(self):
        """_execute_query accepts a custom dispatcher with injected audit logger."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        mock_audit = AsyncMock()
        dispatcher = MessageDispatcher(audit_logger=mock_audit)

        tool_use_event = MagicMock()
        tool_use_event.type = "tool_use"
        tool_use_event.name = "bash"
        tool_use_event.input = {"command": "ls"}

        mock_result = ResultMessage(
            subtype="result",
            duration_ms=500,
            duration_api_ms=200,
            is_error=False,
            num_turns=1,
            session_id="test",
        )

        events = [tool_use_event, mock_result]

        async def mock_query(*, prompt, options):
            for e in events:
                yield e

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider._execute_query(
                prompt="test",
                options=ClaudeAgentOptions(model="claude-sonnet-4-6", cwd="/tmp"),
                dispatcher=dispatcher,
            )

        mock_audit.log_tool_start.assert_awaited_once_with("bash", {"command": "ls"})


class TestCallWithTurnCount:
    """Test that call() passes dispatcher turn_count to _extract_result."""

    @pytest.mark.asyncio
    async def test_call_returns_correct_turn_count(self):
        """call() returns turn_count from dispatcher, not hardcoded 1."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        # Create 3 assistant events to simulate 3 turns
        events = []
        for i in range(3):
            event = MagicMock()
            event.type = "assistant"
            block = MagicMock()
            block.text = f"turn {i + 1}"
            event.content = [block]
            event.error = None
            events.append(event)

        mock_result = ResultMessage(
            subtype="result",
            duration_ms=3000,
            duration_api_ms=1500,
            is_error=False,
            num_turns=3,
            session_id="test",
            total_cost_usd=0.01,
            result="done",
        )
        events.append(mock_result)

        async def mock_query(*, prompt, options):
            for e in events:
                yield e

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider.call(
                prompt="multi-turn test",
                cwd="/tmp",
                model_tier="medium",
            )

        assert result.success is True
        assert result.turns == 3


class TestSpendingCapDetection:
    """Test 3-layer spending cap detection."""

    def test_detect_spending_cap_behavior_trigger(self):
        """Low turns + zero cost + not successful triggers behavioral detection."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = ClaudeRunResult(
            text="",
            success=False,
            cost=0.0,
            turns=0,
        )
        assert provider._detect_spending_cap_behavior(result, turn_count=1) is True

    def test_detect_spending_cap_behavior_no_trigger_success(self):
        """Successful result does not trigger behavioral detection."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = ClaudeRunResult(
            text="done",
            success=True,
            cost=0.0,
            turns=0,
        )
        assert provider._detect_spending_cap_behavior(result, turn_count=1) is False

    def test_detect_spending_cap_behavior_no_trigger_high_turns(self):
        """Multiple turns do not trigger behavioral detection."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = ClaudeRunResult(
            text="",
            success=False,
            cost=0.0,
            turns=3,
        )
        assert provider._detect_spending_cap_behavior(result, turn_count=3) is False

    def test_detect_spending_cap_behavior_no_trigger_nonzero_cost(self):
        """Non-zero cost does not trigger behavioral detection."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = ClaudeRunResult(
            text="",
            success=False,
            cost=0.05,
            turns=0,
        )
        assert provider._detect_spending_cap_behavior(result, turn_count=1) is False

    @pytest.mark.asyncio
    async def test_layer1_message_level_detection(self):
        """Layer 1: spending cap keywords in assistant text set success=False, retryable=True."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        assistant_event = MagicMock()
        assistant_event.type = "assistant"
        block = MagicMock()
        block.text = "your spending limit has been reached"
        assistant_event.content = [block]
        assistant_event.error = None

        mock_result = ResultMessage(
            subtype="result",
            duration_ms=100,
            duration_api_ms=50,
            is_error=False,
            num_turns=1,
            session_id="test",
            total_cost_usd=0.0,
        )

        events = [assistant_event, mock_result]

        async def mock_query(*, prompt, options):
            for e in events:
                yield e

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider.call(
                prompt="do work",
                cwd="/tmp",
                model_tier="medium",
            )

        assert result.success is False
        assert result.retryable is True
        assert "spending cap" in result.error
        assert "message-level" in result.error

    @pytest.mark.asyncio
    async def test_layer2_behavioral_detection(self):
        """Layer 2: low turns + zero cost + failure triggers behavioral detection."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        mock_result = ResultMessage(
            subtype="result",
            duration_ms=100,
            duration_api_ms=50,
            is_error=False,
            num_turns=1,
            session_id="test",
            total_cost_usd=0.0,
        )

        events = [mock_result]

        async def mock_query(*, prompt, options):
            for e in events:
                yield e

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider.call(
                prompt="do work",
                cwd="/tmp",
                model_tier="medium",
            )

        assert result.success is False
        assert result.retryable is True
        assert "behavioral" in result.error

    @pytest.mark.asyncio
    async def test_layer3_exception_detection(self):
        """Layer 3: exception with spending cap keyword triggers _handle_error detection."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        async def mock_query(*, prompt, options):
            raise Exception("spending limit reached")
            yield  # make it a generator

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider.call(
                prompt="do work",
                cwd="/tmp",
                model_tier="medium",
            )

        assert result.success is False
        assert result.retryable is True
        assert "花费上限" in result.error

    @pytest.mark.asyncio
    async def test_no_false_positive_on_success(self):
        """Successful execution is not flagged as spending cap."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        assistant_event = MagicMock()
        assistant_event.type = "assistant"
        block = MagicMock()
        block.text = "completed successfully"
        assistant_event.content = [block]
        assistant_event.error = None

        mock_result = ResultMessage(
            subtype="result",
            duration_ms=2000,
            duration_api_ms=1000,
            is_error=False,
            num_turns=3,
            session_id="test",
            total_cost_usd=0.05,
            result="completed successfully",
        )

        events = [assistant_event, mock_result]

        async def mock_query(*, prompt, options):
            for e in events:
                yield e

        with patch("shannon_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider.call(
                prompt="do work",
                cwd="/tmp",
                model_tier="medium",
            )

        assert result.success is True
        assert result.error is None


class TestHandleErrorClassification:
    """Test _handle_error uses classify_error_for_temporal and sets error_code."""

    def test_auth_error_sets_error_code(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = provider._handle_error(Exception("authentication failed"), 100, "claude-sonnet-4-6")
        assert result.error_code == "AuthenticationError"
        assert result.retryable is False
        assert result.success is False

    def test_permission_error_sets_error_code(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = provider._handle_error(Exception("403 Forbidden"), 100, "claude-sonnet-4-6")
        assert result.error_code == "PermissionError"
        assert result.retryable is False

    def test_rate_limit_sets_error_code(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = provider._handle_error(Exception("rate limit exceeded"), 100, "claude-sonnet-4-6")
        # "rate limit" maps to BillingError in classify_error_for_temporal Level 2
        assert result.error_code == "BillingError"
        assert result.retryable is True

    def test_spending_cap_sets_billing_error_code(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = provider._handle_error(Exception("spending limit reached"), 100, "claude-sonnet-4-6")
        assert result.error_code == "BillingError"
        assert result.retryable is True
        assert result.text != ""

    def test_config_error_sets_error_code(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = provider._handle_error(Exception("ENOENT: no such file"), 100, "claude-sonnet-4-6")
        assert result.error_code == "ConfigurationError"
        assert result.retryable is False

    def test_transient_error_sets_error_code(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = provider._handle_error(Exception("network timeout"), 100, "claude-sonnet-4-6")
        # "timeout" matches RETRYABLE_PATTERNS, but classify_error_for_temporal
        # Level 2 doesn't have a specific "network" or "timeout" pattern,
        # so it falls through to the default: TransientError.
        assert result.error_code == "TransientError"
        assert result.retryable is True

    def test_invalid_target_sets_error_code(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        result = provider._handle_error(Exception("invalid URL format"), 100, "claude-sonnet-4-6")
        assert result.error_code == "InvalidTargetError"
        assert result.retryable is False


class TestRunClaudePromptErrorCode:
    """Test run_claude_prompt sets error_code on error paths."""

    @pytest.mark.asyncio
    async def test_spending_cap_behavior_sets_billing_error_code(self):
        """_is_spending_cap_behavior path sets error_code=BillingError."""
        mock_provider = AsyncMock()
        mock_provider.call = AsyncMock(return_value=ClaudeRunResult(
            text="",
            success=False,
            error="spending limit reached",
            retryable=True,
        ))

        with patch("shannon_core.agents.providers.build_provider_config", return_value=ProviderConfig()):
            with patch("shannon_core.agents.providers.create_provider", return_value=mock_provider):
                result = await run_claude_prompt(
                    prompt="test",
                    repo_path="/tmp",
                )

        assert result.error_code == "BillingError"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_exception_handler_sets_error_code(self):
        """Catch-all exception handler classifies and sets error_code."""
        with patch("shannon_core.agents.providers.build_provider_config", side_effect=Exception("authentication failed")):
            result = await run_claude_prompt(
                prompt="test",
                repo_path="/tmp",
            )

        assert result.success is False
        assert result.error_code == "AuthenticationError"
        assert result.retryable is False


class TestOpenAIProviderTierModelResolution:
    """测试 OpenAIProvider tier-specific 模型解析优先级"""

    def test_tier_specific_override_takes_priority(self):
        """Tier-specific override 优先于 global model 和默认值"""
        config = ProviderConfig(
            type="openai_compatible",
            model="global-model",
            medium_model="custom-medium",
        )
        provider = OpenAIProvider(config)
        assert provider._get_model("medium") == "custom-medium"

    def test_tier_specific_small_model(self):
        """small_model 覆盖 small tier"""
        config = ProviderConfig(
            type="openai_compatible",
            small_model="custom-small",
        )
        provider = OpenAIProvider(config)
        assert provider._get_model("small") == "custom-small"

    def test_tier_specific_large_model(self):
        """large_model 覆盖 large tier"""
        config = ProviderConfig(
            type="openai_compatible",
            large_model="custom-large",
        )
        provider = OpenAIProvider(config)
        assert provider._get_model("large") == "custom-large"

    def test_global_model_used_when_no_tier_override(self):
        """没有 tier override 时使用 global model"""
        config = ProviderConfig(
            type="openai_compatible",
            model="global-model",
            small_model="custom-small",
        )
        provider = OpenAIProvider(config)
        assert provider._get_model("medium") == "global-model"
        assert provider._get_model("small") == "custom-small"

    def test_default_used_when_no_overrides(self):
        """没有覆盖时使用 DEFAULT_MODELS"""
        config = ProviderConfig(type="openai_compatible")
        provider = OpenAIProvider(config)
        assert provider._get_model("small") == "gpt-4o-mini"
        assert provider._get_model("medium") == "gpt-4o"
        assert provider._get_model("large") == "o1"

    def test_tier_override_for_litellm_router(self):
        """LiteLLM router 的 tier override"""
        config = ProviderConfig(
            type="litellm_router",
            medium_model="custom-litellm-medium",
        )
        provider = OpenAIProvider(config)
        assert provider._get_model("medium") == "custom-litellm-medium"
        # small 没有 tier override，使用 litellm_router 默认值
        assert provider._get_model("small") == "anthropic/claude-haiku-4-5"


class TestAnthropicProviderTierModelResolution:
    """测试 AnthropicProvider tier-specific 模型解析优先级"""

    def test_tier_specific_override_takes_priority(self):
        """Tier-specific override 优先于 global model 和默认值"""
        config = ProviderConfig(
            type="anthropic_api",
            model="global-model",
            medium_model="custom-medium",
        )
        provider = AnthropicProvider(config)
        assert provider._get_model("medium") == "custom-medium"

    def test_tier_specific_small_model(self):
        """small_model 覆盖 small tier"""
        config = ProviderConfig(
            type="anthropic_api",
            small_model="custom-small",
        )
        provider = AnthropicProvider(config)
        assert provider._get_model("small") == "custom-small"

    def test_tier_specific_large_model(self):
        """large_model 覆盖 large tier"""
        config = ProviderConfig(
            type="anthropic_api",
            large_model="custom-large",
        )
        provider = AnthropicProvider(config)
        assert provider._get_model("large") == "custom-large"

    def test_global_model_used_when_no_tier_override(self):
        """没有 tier override 时使用 global model"""
        config = ProviderConfig(
            type="anthropic_api",
            model="global-model",
            small_model="custom-small",
        )
        provider = AnthropicProvider(config)
        # medium 没有设置专属覆盖，应使用 global model
        assert provider._get_model("medium") == "global-model"
        # small 有专属覆盖
        assert provider._get_model("small") == "custom-small"

    def test_default_used_when_no_overrides(self):
        """没有覆盖时使用 DEFAULT_MODELS"""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        assert provider._get_model("small") == "claude-haiku-4-5-20251001"
        assert provider._get_model("medium") == "claude-sonnet-4-6"
        assert provider._get_model("large") == "claude-opus-4-8"

    def test_tier_override_for_bedrock(self):
        """Bedrock provider 的 tier override"""
        config = ProviderConfig(
            type="bedrock",
            medium_model="custom-bedrock-medium",
        )
        provider = AnthropicProvider(config)
        assert provider._get_model("medium") == "custom-bedrock-medium"
        # small 没有 tier override，使用 Bedrock 默认值
        assert provider._get_model("small") == "us.anthropic.claude-haiku-4-5"

    def test_tier_override_for_vertex(self):
        """Vertex provider 的 tier override"""
        config = ProviderConfig(
            type="vertex",
            large_model="custom-vertex-large",
        )
        provider = AnthropicProvider(config)
        assert provider._get_model("large") == "custom-vertex-large"
        # medium 没有 tier override，使用 Vertex 默认值
        assert provider._get_model("medium") == "claude-sonnet-4-6@latest"
