"""
测试 Provider 抽象层
"""

import logging
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from supernova_core.agents.providers import (
    AuthenticationError,
    ProviderError,
    RateLimitError,
    SpendingCapError,
    build_provider_config,
    create_provider,
)
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage
from supernova_core.agents.providers_anthropic import AnthropicProvider
from supernova_core.agents.message_dispatcher import MessageDispatcher
from supernova_core.agents.providers_openai import OpenAIProvider
from supernova_core.agents.runner import (
    DEFAULT_MODELS,
    ClaudeRunResult,
    ProviderConfig,
    TokenUsage,
    run_claude_prompt,
)


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

    def test_openai_env_vars(self):
        """测试 openai_compatible 的 SUPERNOVA_OPENAI_* 环境变量(删 fallback 后 openai 读 SUPERNOVA_OPENAI_*)"""
        with patch.dict(os.environ, {
            "SUPERNOVA_AI_PROVIDER": "openai_compatible",
            "SUPERNOVA_OPENAI_API_KEY": "test-key",
            "SUPERNOVA_OPENAI_BASE_URL": "https://api.example.com",
            "SUPERNOVA_MODEL": "gpt-4o",
        }):
            config = build_provider_config()
            assert config.type == "openai_compatible"
            assert config.api_key == "test-key"
            assert config.base_url == "https://api.example.com"
            assert config.model == "gpt-4o"

    def test_anthropic_reads_anthropic_prefixed_vars(self):
        """anthropic_api 直接读 ANTHROPIC_*(无跨前缀 fallback)。"""
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "anthropic-key",
            "ANTHROPIC_BASE_URL": "https://anthropic.example.com",
        }, clear=True):
            config = build_provider_config()
            assert config.api_key == "anthropic-key"
            assert config.base_url == "https://anthropic.example.com"

    def test_anthropic_ignores_shannon_credential_vars(self):
        """anthropic_api 不再读 SUPERNOVA_API_KEY(删 fallback);只认 ANTHROPIC_*。"""
        with patch.dict(os.environ, {
            "SUPERNOVA_API_KEY": "should-be-ignored",
        }, clear=True):
            config = build_provider_config()
            assert config.api_key is None

    def test_explicit_params_override_env(self):
        """测试显式参数覆盖环境变量"""
        with patch.dict(os.environ, {
            "SUPERNOVA_API_KEY": "env-key",
        }):
            config = build_provider_config(api_key="param-key")
            assert config.api_key == "param-key"

    def test_bedrock_config(self):
        """测试 Bedrock 配置"""
        with patch.dict(os.environ, {
            "SUPERNOVA_AI_PROVIDER": "bedrock",
            "AWS_REGION": "us-west-2",
        }):
            config = build_provider_config()
            assert config.type == "bedrock"
            assert config.region == "us-west-2"

    def test_vertex_config(self):
        """测试 Vertex 配置"""
        with patch.dict(os.environ, {
            "SUPERNOVA_AI_PROVIDER": "vertex",
            "SUPERNOVA_PROJECT_ID": "test-project",
            "CLOUD_ML_REGION": "us-central1",
        }):
            config = build_provider_config()
            assert config.type == "vertex"
            assert config.project_id == "test-project"
            assert config.region == "us-central1"

    def test_tier_specific_env_vars(self):
        """测试 SUPERNOVA_*_MODEL 环境变量"""
        with patch.dict(os.environ, {
            "SUPERNOVA_SMALL_MODEL": "custom-small",
            "SUPERNOVA_MEDIUM_MODEL": "custom-medium",
            "SUPERNOVA_LARGE_MODEL": "custom-large",
        }):
            config = build_provider_config()
            assert config.small_model == "custom-small"
            assert config.medium_model == "custom-medium"
            assert config.large_model == "custom-large"

    def test_tier_specific_env_vars_partial(self):
        """测试只设置部分 tier 变量"""
        with patch.dict(os.environ, {
            "SUPERNOVA_MEDIUM_MODEL": "custom-medium",
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
            "SUPERNOVA_MEDIUM_MODEL": "env-medium",
        }):
            config = build_provider_config(medium_model="param-medium")
            assert config.medium_model == "param-medium"


class TestBuildProviderConfigOpenAI:
    """测试 build_provider_config 在 openai 系下的 SUPERNOVA_OPENAI_* 优先级"""

    def test_openai_env_precedence(self, monkeypatch):
        from supernova_core.agents.providers import build_provider_config
        monkeypatch.setenv("SUPERNOVA_AI_PROVIDER", "openai_compatible")
        monkeypatch.setenv("SUPERNOVA_OPENAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
        monkeypatch.setenv("SUPERNOVA_OPENAI_API_KEY", "glm-key")
        monkeypatch.delenv("SUPERNOVA_BASE_URL", raising=False)
        monkeypatch.delenv("SUPERNOVA_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = build_provider_config()
        assert cfg.type == "openai_compatible"
        assert cfg.base_url == "https://open.bigmodel.cn/api/paas/v4"
        assert cfg.api_key == "glm-key"

    def test_openai_no_fallback_to_shannon_vars(self, monkeypatch):
        """openai_compatible 缺 SUPERNOVA_OPENAI_* 时不再回退 SUPERNOVA_*(删 fallback)。"""
        from supernova_core.agents.providers import build_provider_config
        monkeypatch.setenv("SUPERNOVA_AI_PROVIDER", "openai_compatible")
        monkeypatch.delenv("SUPERNOVA_OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("SUPERNOVA_OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("SUPERNOVA_BASE_URL", "https://shared/v4")
        monkeypatch.setenv("SUPERNOVA_API_KEY", "shared-key")
        cfg = build_provider_config()
        assert cfg.base_url is None
        assert cfg.api_key is None

    def test_anthropic_unchanged_by_openai_vars(self, monkeypatch):
        from supernova_core.agents.providers import build_provider_config
        monkeypatch.setenv("SUPERNOVA_AI_PROVIDER", "anthropic_api")
        monkeypatch.setenv("SUPERNOVA_OPENAI_BASE_URL", "https://should-be-ignored/v4")
        cfg = build_provider_config()
        assert cfg.base_url != "https://should-be-ignored/v4"

    def test_openai_tier_models_precedence(self, monkeypatch):
        """openai 系优先读 SUPERNOVA_OPENAI_*_MODEL（模型名与 anthropic 兼容接口不同）"""
        from supernova_core.agents.providers import build_provider_config
        monkeypatch.setenv("SUPERNOVA_AI_PROVIDER", "openai_compatible")
        monkeypatch.setenv("SUPERNOVA_LARGE_MODEL", "GLM-5.2[1m]")
        monkeypatch.setenv("SUPERNOVA_MEDIUM_MODEL", "GLM-5.2[1m]")
        monkeypatch.setenv("SUPERNOVA_SMALL_MODEL", "GLM-4.5-Air")
        monkeypatch.setenv("SUPERNOVA_OPENAI_LARGE_MODEL", "glm-5.2")
        monkeypatch.setenv("SUPERNOVA_OPENAI_MEDIUM_MODEL", "glm-5.2")
        monkeypatch.setenv("SUPERNOVA_OPENAI_SMALL_MODEL", "glm-4.5-air")
        cfg = build_provider_config()
        assert cfg.large_model == "glm-5.2"
        assert cfg.medium_model == "glm-5.2"
        assert cfg.small_model == "glm-4.5-air"

    def test_openai_tier_models_no_fallback(self, monkeypatch):
        """openai_compatible 缺 SUPERNOVA_OPENAI_*_MODEL 时不再回退 SUPERNOVA_*_MODEL。"""
        from supernova_core.agents.providers import build_provider_config
        monkeypatch.setenv("SUPERNOVA_AI_PROVIDER", "openai_compatible")
        monkeypatch.delenv("SUPERNOVA_OPENAI_LARGE_MODEL", raising=False)
        monkeypatch.delenv("SUPERNOVA_OPENAI_MEDIUM_MODEL", raising=False)
        monkeypatch.delenv("SUPERNOVA_OPENAI_SMALL_MODEL", raising=False)
        monkeypatch.setenv("SUPERNOVA_MEDIUM_MODEL", "shared-model")
        cfg = build_provider_config()
        assert cfg.medium_model is None  # 不回退 SUPERNOVA_MEDIUM_MODEL

    def test_anthropic_tier_models_ignore_openai(self, monkeypatch):
        """anthropic 系不读 SUPERNOVA_OPENAI_*_MODEL"""
        from supernova_core.agents.providers import build_provider_config
        monkeypatch.setenv("SUPERNOVA_AI_PROVIDER", "anthropic_api")
        monkeypatch.setenv("SUPERNOVA_MEDIUM_MODEL", "GLM-5.2[1m]")
        monkeypatch.setenv("SUPERNOVA_OPENAI_MEDIUM_MODEL", "glm-5.2")
        cfg = build_provider_config()
        assert cfg.medium_model == "GLM-5.2[1m]"


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

        with patch("supernova_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider.call(
                prompt="Test prompt",
                cwd="/tmp",
                model_tier="medium",
            )

        assert result.success is True
        assert result.text == "Test response"
        assert result.tokens.input_tokens == 100
        assert result.tokens.output_tokens == 50
        # claude-sonnet-4-6（medium tier 默认）不在 GLM 价表 → 自算回落 0.0（不假估算，spec §4.5）
        assert result.cost == 0.0
        assert result.cost_currency == "CNY"

    def test_extract_tokens_from_dict_usage(self):
        """回归守护：claude-agent-sdk ResultMessage.usage 是 dict（非对象）。

        getattr(dict, key) 永远返回默认值——曾致 claude 引擎全 profile cost=0
        （含智谱 glm-anthropic：CLI 给非零 usage dict 也被读成 0）。dict 必须用
        .get() 访问。智谱 glm-4.5-air 实测值（input=31567/output=108/cache_read=1984）。
        """
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        result = ResultMessage(
            subtype="result",
            duration_ms=1000,
            duration_api_ms=500,
            is_error=False,
            num_turns=1,
            session_id="test",
            usage={
                "input_tokens": 31567,
                "output_tokens": 108,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 1984,
            },
        )

        tokens = provider._extract_tokens(result)
        assert tokens.input_tokens == 31567
        assert tokens.output_tokens == 108
        assert tokens.cache_read_input_tokens == 1984
        assert tokens.cache_creation_input_tokens == 0


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

    def test_build_options_uses_max_turns_override(self, monkeypatch):
        """B2: _build_options(max_turns_override=N) → options.max_turns == N。"""
        monkeypatch.setenv("CLAUDE_MAX_TURNS", "200")  # 默认值
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        with patch.object(provider, "_is_adaptive_thinking_enabled", return_value=False):
            options = provider._build_options(
                cwd="/tmp", model="claude-sonnet-4-6", output_format=None, max_turns_override=500,
            )
        assert options.max_turns == 500

    def test_build_options_falls_back_to_env_when_override_none(self, monkeypatch):
        """override=None → 沿用 CLAUDE_MAX_TURNS env。"""
        monkeypatch.setenv("CLAUDE_MAX_TURNS", "200")
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        with patch.object(provider, "_is_adaptive_thinking_enabled", return_value=False):
            options = provider._build_options(
                cwd="/tmp", model="claude-sonnet-4-6", output_format=None, max_turns_override=None,
            )
        assert options.max_turns == 200

    def test_build_sdk_env_sets_is_sandbox_when_root(self, monkeypatch):
        """root(getuid==0) 下 options.env 设 IS_SANDBOX=1, 绕过 claude CLI root 守卫
        (bypassPermissions + root → exit(1) "cannot be used with root/sudo"). worker
        容器 root 跑 LLM agent 必需(2026-07-14 bug#4)."""
        monkeypatch.setattr("os.getuid", lambda: 0)
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        options = provider._build_options(cwd="/tmp", model="claude-sonnet-4-6")
        assert options.env.get("IS_SANDBOX") == "1"

    def test_build_sdk_env_no_is_sandbox_when_non_root(self, monkeypatch):
        """非 root(getuid!=0) 不设 IS_SANDBOX: host supernova-user 跑 claude CLI 不触发 root
        守卫(getuid!=0), 不污染 host 路径(保持 host 与 worker 两路互不干扰)."""
        monkeypatch.setattr("os.getuid", lambda: 1000)
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        options = provider._build_options(cwd="/tmp", model="claude-sonnet-4-6")
        assert "IS_SANDBOX" not in (options.env or {})


class TestOpenAIProvider:
    def test_get_model_resolves_tier(self):
        from supernova_core.agents.providers_openai import OpenAIProvider
        config = ProviderConfig(
            type="openai_compatible",
            medium_model="GLM-5.2[1m]",
        )
        provider = OpenAIProvider(config)
        assert provider._get_model("medium") == "GLM-5.2[1m]"

    def test_get_model_falls_back_to_default(self):
        from supernova_core.agents.providers_openai import OpenAIProvider
        config = ProviderConfig(type="openai_compatible")
        provider = OpenAIProvider(config)
        # DEFAULT_MODELS["openai_compatible"]["medium"]
        assert provider._get_model("medium") == DEFAULT_MODELS["openai_compatible"]["medium"]

    def test_build_agent_wires_chatcompletions_model_and_tools(self):
        from agents import Agent, OpenAIChatCompletionsModel
        from supernova_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k", medium_model="m"))
        agent = provider.build_agent("m", output_format=None)
        assert isinstance(agent, Agent)
        assert isinstance(agent.model, OpenAIChatCompletionsModel)
        assert agent.name == "shannon-openai-agent"
        # 原：工具集非空（不再断言固定 count，因 task tool 等横向计划会增减工具集）
        assert len(agent.tools) > 0

    def test_build_agent_no_output_type_even_when_output_format_given(self):
        """openai 引擎不设 output_type（第三方端点不支持 response_format json_schema，传之必 400，2026-07-24）；结构化输出靠本地 L0 解析。"""
        from supernova_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k", medium_model="m"))
        schema = {"type": "object", "properties": {"verdict": {"type": "string"}}, "required": ["verdict"]}
        agent = provider.build_agent("m", output_format=schema)
        assert agent.output_type is None

    def test_build_agent_output_type_none_when_no_output_format(self):
        """B2: output_format 为 None 时，output_type 必须为 None（兼容纯文本路径）。"""
        from supernova_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k", medium_model="m"))
        agent = provider.build_agent("m", output_format=None)
        assert agent.output_type is None

    @pytest.mark.asyncio
    async def test_call_maps_result_and_audits(self, monkeypatch, tmp_path):
        # 用 mock Runner.run_streamed 验证 call() 的组装：event 收集 + 映射 + audit
        from unittest.mock import AsyncMock, MagicMock
        from supernova_core.agents.providers_openai import OpenAIProvider

        config = ProviderConfig(type="openai_compatible", base_url="https://x/v4", api_key="k", medium_model="m")
        provider = OpenAIProvider(config)

        async def _empty():  # stream_events 占位迭代器
            if False:
                yield  # 让它成为 async generator

        fake_result = MagicMock()
        fake_result.final_output = "done"
        fake_result.context_wrapper = MagicMock()
        fake_result.context_wrapper.usage = MagicMock(input_tokens=3, output_tokens=2, input_tokens_details=None)
        fake_result.stream_events = _empty

        monkeypatch.setattr("supernova_core.agents.providers_openai.Runner.run_streamed",
                            MagicMock(return_value=fake_result))

        audit = AsyncMock()
        res = await provider.call(prompt="hi", cwd=str(tmp_path), model_tier="medium", audit_logger=audit)
        assert res.success is True
        assert res.text == "done"
        assert res.model == "m"
        assert res.tokens.input_tokens == 3

    @pytest.mark.asyncio
    async def test_call_handles_max_turns(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock
        from agents import MaxTurnsExceeded
        from supernova_core.agents.providers_openai import OpenAIProvider

        config = ProviderConfig(type="openai_compatible", base_url="https://x/v4", api_key="k", medium_model="m")
        provider = OpenAIProvider(config)

        async def _raising_stream():
            raise MaxTurnsExceeded("hit")
            yield  # 使其成为 async generator

        fake_result = MagicMock()
        fake_result.stream_events = _raising_stream
        monkeypatch.setattr("supernova_core.agents.providers_openai.Runner.run_streamed",
                            MagicMock(return_value=fake_result))
        res = await provider.call(prompt="hi", cwd=str(tmp_path), model_tier="medium")
        assert res.stop_reason == "max_turns"
        # B1: max_turns 必须反映为失败（对齐 Claude subtype=error_max_turns）
        assert res.success is False
        assert res.error_code == "ExecutionLimitError"
        assert res.retryable is False

    def test_is_retryable_classifies_rate_limit(self):
        from supernova_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
        assert provider._is_retryable_error(Exception("Rate limit exceeded")) is True
        assert provider._is_retryable_error(Exception("request timed out")) is True
        assert provider._is_retryable_error(Exception("Service unavailable (503)")) is True
        assert provider._is_retryable_error(Exception("invalid_api_key (401)")) is False
        assert provider._is_retryable_error(Exception("permission denied (403)")) is False
        assert provider._is_retryable_error(Exception("some transient error")) is True

    def test_classify_error_rate_limit(self):
        from supernova_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
        code, retryable = provider._classify_error(Exception("Rate limit exceeded"))
        assert code == "RateLimitError"
        assert retryable is True

    def test_classify_error_timeout(self):
        from supernova_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
        code, retryable = provider._classify_error(TimeoutError("request timed out"))
        assert code == "TimeoutError"
        assert retryable is False  # 整体超时(CALL_TIMEOUT)确定性,non_retryable

    def test_classify_error_auth(self):
        from supernova_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
        code, retryable = provider._classify_error(Exception("invalid_api_key (401)"))
        assert code == "AuthenticationError"
        assert retryable is False

    def test_classify_error_permission(self):
        from supernova_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
        code, retryable = provider._classify_error(Exception("permission denied (403)"))
        assert code == "PermissionError"
        assert retryable is False

    def test_classify_error_default_agent_execution(self):
        from supernova_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
        code, retryable = provider._classify_error(Exception("some transient error"))
        assert code == "AgentExecutionError"
        assert retryable is True

    def test_classify_error_response_format_400_non_retryable(self):
        """第三方端点不支持 response_format json_schema -> 400 invalid_request，
        参数级永久拒绝，重试无意义（2026-07-24 致 injection/authz/auth/ssrf 四个
        *-vuln agent 8/8 空转根因）。须判 non-retryable，避免被 "unavailable" 误判。"""
        from supernova_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
        msg = ("Error code: 400 - {'error': {'message': 'This response_format type "
               "is unavailable now', 'type': 'invalid_request_error'}}")
        code, retryable = provider._classify_error(Exception(msg))
        assert code == "BadRequestError"
        assert retryable is False

    @pytest.mark.asyncio
    async def test_call_l1_reparse_when_l0_fails(self, monkeypatch, tmp_path):
        """openai 引擎不设 output_type -> L0 在 map_run_result；final 非法 JSON
        (structured_output None) 时 L1 _lightweight_reparse 兜底恢复 structured_output（2026-07-24）。"""
        from unittest.mock import AsyncMock, MagicMock
        from supernova_core.agents.providers_openai import OpenAIProvider, _ReparsedRunResult

        config = ProviderConfig(type="openai_compatible", base_url="https://x/v4", api_key="k", medium_model="m")
        provider = OpenAIProvider(config)

        async def _empty():
            if False:
                yield

        fake_result = MagicMock()
        fake_result.final_output = "not json at all, no braces here"
        fake_result.context_wrapper = MagicMock()
        fake_result.context_wrapper.usage = MagicMock(input_tokens=1, output_tokens=1, input_tokens_details=None)
        fake_result.stream_events = _empty
        monkeypatch.setattr("supernova_core.agents.providers_openai.Runner.run_streamed",
                            MagicMock(return_value=fake_result))

        class _FakeSC:
            def __init__(self, *a, **kw):
                self.text = "not json at all, no braces here"
                self.turns = 1
                self.tool_call_count = 0
            async def on_event(self, event):
                pass
            async def close(self):
                pass
        monkeypatch.setattr("supernova_core.agents.providers_openai.StreamCollector", _FakeSC)

        reparsed = _ReparsedRunResult({"verdict": "vulnerable"}, input_tokens=2, output_tokens=2)
        monkeypatch.setattr(provider, "_lightweight_reparse", AsyncMock(return_value=reparsed))

        res = await provider.call(prompt="hi", cwd=str(tmp_path), model_tier="medium",
                                  output_format={"type": "object"})
        assert res.success is True
        assert res.structured_output == {"verdict": "vulnerable"}

    def test_handle_error_sets_error_code(self):
        """B3: _handle_error 必须填 error_code（此前恒 None）。"""
        from supernova_core.agents.providers_openai import OpenAIProvider
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k", medium_model="m"))
        res = provider._handle_error(Exception("Rate limit exceeded"), duration=100, model="m")
        assert res.success is False
        assert res.error_code == "RateLimitError"
        assert res.retryable is True


class TestOpenAISubagentMaxTurns:
    """B2: openai 子代理 max_turns 默认 100（对称主 agent _max_turns()）。"""

    def test_default_is_100(self, monkeypatch):
        monkeypatch.delenv("SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS", raising=False)
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible"))
        assert provider._subagent_max_turns() == 100

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS", "60")
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible"))
        assert provider._subagent_max_turns() == 60


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
        """_execute_query collects text via dispatcher from a real AssistantMessage."""
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        from claude_agent_sdk import AssistantMessage, TextBlock
        assistant_event = AssistantMessage(
            content=[TextBlock(text="partial "), TextBlock(text="response")],
            model="test-model",
        )

        mock_result = ResultMessage(
            subtype="result",
            duration_ms=1000,
            duration_api_ms=500,
            is_error=False,
            num_turns=1,
            session_id="test",
        )

        async def mock_query(*, prompt, options):
            yield assistant_event
            yield mock_result

        with patch("supernova_core.agents.providers_anthropic.query", side_effect=mock_query):
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

        from claude_agent_sdk import AssistantMessage, ToolUseBlock
        tool_use_event = AssistantMessage(
            content=[ToolUseBlock(id="call_bash", name="bash", input={"command": "ls"})],
            model="test-model",
        )

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

        with patch("supernova_core.agents.providers_anthropic.query", side_effect=mock_query):
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
        from claude_agent_sdk import AssistantMessage, TextBlock
        events = []
        for i in range(3):
            events.append(AssistantMessage(
                content=[TextBlock(text=f"turn {i + 1}")], model="test-model",
            ))

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

        with patch("supernova_core.agents.providers_anthropic.query", side_effect=mock_query):
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

        from claude_agent_sdk import AssistantMessage, TextBlock
        assistant_event = AssistantMessage(
            content=[TextBlock(text="your spending limit has been reached")],
            model="test-model",
        )

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

        with patch("supernova_core.agents.providers_anthropic.query", side_effect=mock_query):
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

        with patch("supernova_core.agents.providers_anthropic.query", side_effect=mock_query):
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

        with patch("supernova_core.agents.providers_anthropic.query", side_effect=mock_query):
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

        from claude_agent_sdk import AssistantMessage, TextBlock
        assistant_event = AssistantMessage(
            content=[TextBlock(text="completed successfully")],
            model="test-model",
        )

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

        with patch("supernova_core.agents.providers_anthropic.query", side_effect=mock_query):
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

        with patch("supernova_core.agents.providers.build_provider_config", return_value=ProviderConfig()):
            with patch("supernova_core.agents.providers.create_provider", return_value=mock_provider):
                result = await run_claude_prompt(
                    prompt="test",
                    repo_path="/tmp",
                )

        assert result.error_code == "BillingError"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_exception_handler_sets_error_code(self):
        """Catch-all exception handler classifies and sets error_code."""
        with patch("supernova_core.agents.providers.build_provider_config", side_effect=Exception("authentication failed")):
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


class TestTierModelEnvVarIntegration:
    """端到端测试：环境变量 → build_provider_config → Provider._get_model()"""

    def test_single_tier_override_others_use_defaults(self):
        """设置 SUPERNOVA_MEDIUM_MODEL 后，只有 medium tier 被覆盖"""
        with patch.dict(os.environ, {
            "SUPERNOVA_MEDIUM_MODEL": "gpt-4o",
        }, clear=True):
            config = build_provider_config()
            provider = AnthropicProvider(config)

        assert provider._get_model("medium") == "gpt-4o"
        assert provider._get_model("small") == "claude-haiku-4-5-20251001"
        assert provider._get_model("large") == "claude-opus-4-8"

    def test_tier_override_plus_global_fallback(self):
        """SUPERNOVA_MODEL + SUPERNOVA_LARGE_MODEL：large 用 tier override，其余用 global"""
        with patch.dict(os.environ, {
            "SUPERNOVA_MODEL": "fallback-model",
            "SUPERNOVA_LARGE_MODEL": "custom-large",
        }, clear=True):
            config = build_provider_config()
            provider = AnthropicProvider(config)

        assert provider._get_model("large") == "custom-large"
        assert provider._get_model("medium") == "fallback-model"
        assert provider._get_model("small") == "fallback-model"

    def test_no_overrides_all_defaults(self):
        """不设置任何覆盖变量，所有 tier 使用 DEFAULT_MODELS"""
        with patch.dict(os.environ, {}, clear=True):
            config = build_provider_config()
            provider = AnthropicProvider(config)

        assert provider._get_model("small") == "claude-haiku-4-5-20251001"
        assert provider._get_model("medium") == "claude-sonnet-4-6"
        assert provider._get_model("large") == "claude-opus-4-8"

    def test_bedrock_tier_override_with_env(self):
        """Bedrock provider 通过环境变量覆盖 tier"""
        with patch.dict(os.environ, {
            "SUPERNOVA_AI_PROVIDER": "bedrock",
            "SUPERNOVA_SMALL_MODEL": "custom-bedrock-small",
        }, clear=True):
            config = build_provider_config()
            provider = AnthropicProvider(config)

        assert provider._get_model("small") == "custom-bedrock-small"
        assert provider._get_model("medium") == "us.anthropic.claude-sonnet-4-6"

    def test_vertex_tier_override_with_env(self):
        """Vertex provider 通过环境变量覆盖 tier"""
        with patch.dict(os.environ, {
            "SUPERNOVA_AI_PROVIDER": "vertex",
            "SUPERNOVA_LARGE_MODEL": "custom-vertex-large",
        }, clear=True):
            config = build_provider_config()
            provider = AnthropicProvider(config)

        assert provider._get_model("large") == "custom-vertex-large"
        assert provider._get_model("small") == "claude-haiku-4-5@latest"

    def test_openai_tier_override_with_env(self):
        """OpenAI compatible provider 通过 SUPERNOVA_OPENAI_*_MODEL 覆盖 tier"""
        with patch.dict(os.environ, {
            "SUPERNOVA_AI_PROVIDER": "openai_compatible",
            "SUPERNOVA_OPENAI_MEDIUM_MODEL": "gpt-4o-turbo",
        }, clear=True):
            config = build_provider_config()
            provider = OpenAIProvider(config)

        assert provider._get_model("medium") == "gpt-4o-turbo"
        assert provider._get_model("small") == "gpt-4o-mini"

    def test_litellm_tier_override_with_env(self):
        """LiteLLM router 通过环境变量覆盖 tier"""
        with patch.dict(os.environ, {
            "SUPERNOVA_AI_PROVIDER": "litellm_router",
            "SUPERNOVA_LARGE_MODEL": "anthropic/claude-opus-4-8-custom",
        }, clear=True):
            config = build_provider_config()
            provider = OpenAIProvider(config)

        assert provider._get_model("large") == "anthropic/claude-opus-4-8-custom"
        assert provider._get_model("medium") == "anthropic/claude-sonnet-4-6"

    def test_all_three_tiers_overridden(self):
        """三个 tier 全部覆盖"""
        with patch.dict(os.environ, {
            "SUPERNOVA_SMALL_MODEL": "my-small",
            "SUPERNOVA_MEDIUM_MODEL": "my-medium",
            "SUPERNOVA_LARGE_MODEL": "my-large",
        }, clear=True):
            config = build_provider_config()
            provider = AnthropicProvider(config)

        assert provider._get_model("small") == "my-small"
        assert provider._get_model("medium") == "my-medium"
        assert provider._get_model("large") == "my-large"

    def test_tier_override_beats_shannon_model(self):
        """SUPERNOVA_*_MODEL 优先级高于 SUPERNOVA_MODEL"""
        with patch.dict(os.environ, {
            "SUPERNOVA_MODEL": "global-model",
            "SUPERNOVA_MEDIUM_MODEL": "tier-medium",
        }, clear=True):
            config = build_provider_config()
            provider = AnthropicProvider(config)

        assert provider._get_model("medium") == "tier-medium"
        assert provider._get_model("small") == "global-model"


class TestBuildOptionsMaxTurns:
    """L2 (precondition): _build_options sets max_turns (default 10000, env-overridable)."""

    def test_default_max_turns(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        with patch.dict(os.environ, {}, clear=True):
            options = provider._build_options(cwd="/tmp", model="claude-sonnet-4-6")
        assert options.max_turns == 10000

    def test_env_override_max_turns(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)
        with patch.dict(os.environ, {"CLAUDE_MAX_TURNS": "50"}, clear=True):
            options = provider._build_options(cwd="/tmp", model="claude-sonnet-4-6")
        assert options.max_turns == 50


class TestExecuteQueryMountsResultMetadata:
    """L1: _execute_query mounts dispatcher result-metadata onto the final ResultMessage."""

    @pytest.mark.asyncio
    async def test_mounts_failure_metadata(self):
        config = ProviderConfig(type="anthropic_api")
        provider = AnthropicProvider(config)

        mock_result = ResultMessage(
            subtype="error_max_turns",
            duration_ms=1000,
            duration_api_ms=500,
            is_error=True,
            num_turns=200,
            session_id="test",
            stop_reason="end_turn",
            permission_denials=[{"tool": "bash"}],
            api_error_status=429,
            errors=["max turns"],
        )

        async def mock_query(*, prompt, options):
            yield mock_result

        with patch("supernova_core.agents.providers_anthropic.query", side_effect=mock_query):
            final = await provider._execute_query(
                prompt="test",
                options=ClaudeAgentOptions(model="claude-sonnet-4-6", cwd="/tmp"),
            )

        assert final.result_is_error is True
        assert final.result_subtype == "error_max_turns"
        assert final.stop_reason == "end_turn"
        assert final.permission_denials == [{"tool": "bash"}]
        assert final.api_error_status == 429
        assert final.result_errors == ["max turns"]


class TestStopReasonFields:
    """L2: ClaudeRunResult and AgentMetrics carry stop_reason."""

    def test_claude_run_result_has_stop_reason(self):
        from supernova_core.agents.runner import ClaudeRunResult
        result = ClaudeRunResult(text="ok", success=True, stop_reason="end_turn")
        assert result.stop_reason == "end_turn"

    def test_claude_run_result_stop_reason_default_none(self):
        from supernova_core.agents.runner import ClaudeRunResult
        assert ClaudeRunResult().stop_reason is None

    def test_agent_metrics_has_stop_reason(self):
        from supernova_core.models.metrics import AgentMetrics
        metrics = AgentMetrics(duration_ms=10, stop_reason="max_duration")
        assert metrics.stop_reason == "max_duration"

    def test_agent_metrics_stop_reason_default_none(self):
        from supernova_core.models.metrics import AgentMetrics
        assert AgentMetrics(duration_ms=10).stop_reason is None


def _result_msg(**mounted):
    """Build a ResultMessage and apply mounted metadata attrs (as _execute_query does)."""
    msg = ResultMessage(
        subtype="result",
        duration_ms=10,
        duration_api_ms=5,
        is_error=False,
        num_turns=1,
        session_id="test",
    )
    for k, v in mounted.items():
        setattr(msg, k, v)
    return msg


class TestExtractResultFailureSemantics:
    """L2: _extract_result derives success from is_error/subtype and persists stop_reason."""

    def test_success_when_no_error(self):
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
        msg = _result_msg(result_is_error=False, result_subtype="result", stop_reason="end_turn")
        result = provider._extract_result(msg, duration=10, model="m", turn_count=1)
        assert result.success is True
        assert result.stop_reason == "end_turn"

    def test_is_error_sets_failure(self):
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
        msg = _result_msg(result_is_error=True, result_subtype="result")
        result = provider._extract_result(msg, duration=10, model="m", turn_count=1)
        assert result.success is False

    def test_error_subtype_sets_failure(self):
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
        # is_error not set True, but subtype starts with error_ -> still failure
        msg = _result_msg(result_is_error=False, result_subtype="error_max_turns")
        result = provider._extract_result(msg, duration=10, model="m", turn_count=1)
        assert result.success is False

    def test_stop_reason_persisted(self):
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
        msg = _result_msg(result_is_error=False, result_subtype="result", stop_reason="refusal")
        result = provider._extract_result(msg, duration=10, model="m", turn_count=1)
        assert result.stop_reason == "refusal"


class TestExtractResultStructuredOutputFallback:
    """Anthropic structured_output 兜底：SDK 没解析出时从 collected_text 提取 JSON。

    GLM 后端下 CLI SDK 的 result_message.structured_output 常为 None（final 文本
    夹中文说明+JSON），导致 vuln agent 的 {vt}_exploitation_queue.json 不落盘、
    黑盒 preflight 误报 "No whitebox results"。此处对齐 openai 引擎兜底。
    """

    def setup_method(self):
        self.provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))

    def test_fallback_recovers_json_from_mixed_text(self):
        # GLM 典型形态：中文前导叙述 + ```json fence 包裹的 queue
        text = (
            "分析完成，漏洞队列如下：\n"
            "```json\n"
            '{"vulnerabilities": [{"ID": "X"}]}\n'
            "```\n"
            "以上。"
        )
        msg = _result_msg(collected_text=text)  # structured_output 缺省 None
        result = self.provider._extract_result(
            msg, duration=10, model="m", turn_count=1,
            output_format={"type": "object"},
        )
        assert result.structured_output == {"vulnerabilities": [{"ID": "X"}]}

    def test_sdk_structured_output_wins_over_fallback(self):
        # SDK 已解析出 structured_output 时不用兜底（优先级，避免劣化）
        text = '前置说明 {"should_not_be_used": true} 收尾'
        msg = _result_msg(structured_output={"from": "sdk"}, collected_text=text)
        result = self.provider._extract_result(
            msg, duration=10, model="m", turn_count=1,
            output_format={"type": "object"},
        )
        assert result.structured_output == {"from": "sdk"}

    def test_no_output_format_skips_fallback(self):
        # 非结构化 agent（recon/report）不传 output_format → 不触发兜底
        text = '纯叙述，含个 {"k": 1} 但不该被当 structured_output'
        msg = _result_msg(collected_text=text)
        result = self.provider._extract_result(
            msg, duration=10, model="m", turn_count=1,
        )
        assert result.structured_output is None

    def test_fallback_garbage_text_returns_none(self):
        msg = _result_msg(collected_text="纯叙述收尾，没有 JSON")
        result = self.provider._extract_result(
            msg, duration=10, model="m", turn_count=1,
            output_format={"type": "object"},
        )
        assert result.structured_output is None


class TestExtractCostSelfComputed:
    """claude 引擎自算 cost（spec §4.5）：_extract_cost 用 tokens×价表，不读 SDK total_cost_usd。"""

    def test_extract_cost_ignores_sdk_total_cost_usd(self):
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
        mock_usage = MagicMock()
        mock_usage.input_tokens = 1_000_000
        mock_usage.output_tokens = 0
        mock_usage.cache_creation_input_tokens = 0
        mock_usage.cache_read_input_tokens = 0
        msg = ResultMessage(
            subtype="result",
            duration_ms=10,
            duration_api_ms=5,
            is_error=False,
            num_turns=1,
            session_id="s",
            total_cost_usd=999.0,  # SDK 假高值，必须被忽略
            usage=mock_usage,
            result="ok",
        )
        result = provider._extract_result(msg, duration=10, model="glm-5.2")
        assert result.cost == 50.0  # 1M input × 50 / 1M（CNY 本币），不是 999
        assert result.cost_currency == "CNY"


class TestClassifyResultFailure:
    """L2: _classify_result_failure maps structured signals to (error_code, retryable)."""

    def setup_method(self):
        self.provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))

    def test_error_max_turns(self):
        assert self.provider._classify_result_failure("error_max_turns", True, None, None) == ("ExecutionLimitError", False)

    def test_error_during_execution(self):
        assert self.provider._classify_result_failure("error_during_execution", True, None, None) == ("TransientError", True)

    def test_error_max_structured_output_retries(self):
        assert self.provider._classify_result_failure("error_max_structured_output_retries", True, None, None) == ("OutputValidationError", True)

    def test_429_rate_limit(self):
        assert self.provider._classify_result_failure("result", True, 429, None) == ("RateLimitError", True)

    def test_500_server_transient(self):
        assert self.provider._classify_result_failure("result", True, 500, None) == ("TransientError", True)

    def test_529_overloaded_transient(self):
        assert self.provider._classify_result_failure("result", True, 529, None) == ("TransientError", True)

    def test_402_billing(self):
        assert self.provider._classify_result_failure("result", True, 402, None) == ("BillingError", True)

    def test_401_authentication(self):
        assert self.provider._classify_result_failure("result", True, 401, None) == ("AuthenticationError", False)

    def test_403_permission(self):
        assert self.provider._classify_result_failure("result", True, 403, None) == ("PermissionError", False)

    def test_subtype_beats_api_status(self):
        # error_max_turns subtype wins even when an api_error_status is present
        assert self.provider._classify_result_failure("error_max_turns", True, 429, None) == ("ExecutionLimitError", False)

    def test_fallback_uses_errors_text(self):
        # is_error but no special subtype and no api_error_status -> text fallback
        code, retryable = self.provider._classify_result_failure("result", True, None, ["network timeout"])
        assert code == "TransientError"  # classify_error_for_temporal("network timeout") default
        assert retryable is True

    def test_fallback_default_message(self):
        code, retryable = self.provider._classify_result_failure("result", True, None, None)
        assert code == "TransientError"
        assert retryable is True


def _stream(result_message, *events):
    """Build a mock query that yields events then the terminal ResultMessage."""
    async def mock_query(*, prompt, options):
        for e in events:
            yield e
        yield result_message
    return mock_query


class TestCallResultFailureLayer:
    """L2: call() classifies structured result failures and emits diagnostics."""

    @pytest.mark.asyncio
    async def test_error_max_turns_via_call(self):
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
        msg = ResultMessage(
            subtype="error_max_turns",
            duration_ms=1000, duration_api_ms=500,
            is_error=True, num_turns=200, session_id="t",
        )
        with patch("supernova_core.agents.providers_anthropic.query", side_effect=_stream(msg)):
            result = await provider.call(prompt="p", cwd="/tmp", model_tier="medium")
        assert result.success is False
        assert result.error_code == "ExecutionLimitError"
        assert result.retryable is False
        assert "SDK result failure" in (result.error or "")

    @pytest.mark.asyncio
    async def test_api_error_429_via_call(self):
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
        msg = ResultMessage(
            subtype="result",
            duration_ms=100, duration_api_ms=50,
            is_error=True, num_turns=1, session_id="t",
            api_error_status=429,
        )
        with patch("supernova_core.agents.providers_anthropic.query", side_effect=_stream(msg)):
            result = await provider.call(prompt="p", cwd="/tmp", model_tier="medium")
        assert result.success is False
        assert result.error_code == "RateLimitError"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_logs_non_end_turn_stop_reason(self, caplog):
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
        msg = ResultMessage(
            subtype="result",
            duration_ms=100, duration_api_ms=50,
            is_error=False, num_turns=1, session_id="t",
            stop_reason="max_duration",
        )
        with caplog.at_level(logging.WARNING, logger="supernova_core.agents.providers_anthropic"):
            with patch("supernova_core.agents.providers_anthropic.query", side_effect=_stream(msg)):
                result = await provider.call(prompt="p", cwd="/tmp", model_tier="medium")
        assert result.success is True  # non-end_turn stop is diagnostic, not failure
        assert any("stop_reason" in r.getMessage() and "max_duration" in r.getMessage()
                   for r in caplog.records)

    @pytest.mark.asyncio
    async def test_logs_stop_sequence_as_benign(self, caplog):
        # stop_sequence 来自 CLI/协议层透传（项目不配置），属良性诊断：
        # 文案应点名 "typically harmless"，且 success 仍为 True（不触发失败/重试）。
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
        msg = ResultMessage(
            subtype="result",
            duration_ms=100, duration_api_ms=50,
            is_error=False, num_turns=1, session_id="t",
            stop_reason="stop_sequence",
        )
        with caplog.at_level(logging.WARNING, logger="supernova_core.agents.providers_anthropic"):
            with patch("supernova_core.agents.providers_anthropic.query", side_effect=_stream(msg)):
                result = await provider.call(prompt="p", cwd="/tmp", model_tier="medium")
        assert result.success is True
        assert any("stop_sequence" in r.getMessage() and "typically harmless" in r.getMessage()
                   for r in caplog.records)

    @pytest.mark.asyncio
    async def test_logs_permission_denials(self, caplog):
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
        msg = ResultMessage(
            subtype="result",
            duration_ms=100, duration_api_ms=50,
            is_error=False, num_turns=1, session_id="t",
            permission_denials=[{"tool": "bash"}],
        )
        with caplog.at_level(logging.INFO, logger="supernova_core.agents.providers_anthropic"):
            with patch("supernova_core.agents.providers_anthropic.query", side_effect=_stream(msg)):
                await provider.call(prompt="p", cwd="/tmp", model_tier="medium")
        assert any("permission denials" in r.getMessage().lower() for r in caplog.records)


class TestStopReasonPropagationAndNonRetryable:
    """L2: executor maps stop_reason to AgentMetrics; error_max_turns stays non-retryable end-to-end."""

    def test_classify_chain_error_max_turns_is_non_retryable(self):
        """error_max_turns (retryable=False) reaches ApplicationFailure(non_retryable=True)."""
        from supernova_core.models.errors import ErrorCode, PentestError, classify_error_for_temporal
        err = PentestError(
            "max turns reached",
            category="validation",
            retryable=False,
            error_code=ErrorCode.AGENT_EXECUTION_FAILED,
        )
        error_type, retryable = classify_error_for_temporal(err)
        assert error_type == "AgentExecutionError"
        assert retryable is False
        non_retryable = not retryable
        assert non_retryable is True

    def test_agent_metrics_stop_reason_round_trip(self):
        from supernova_core.models.metrics import AgentMetrics
        metrics = AgentMetrics(duration_ms=10, stop_reason="end_turn")
        dumped = metrics.model_dump()
        assert dumped["stop_reason"] == "end_turn"


class TestProviderAuditLoggerInjection:
    """L3: provider.call / _execute_query thread a ToolAuditLogger into the dispatcher."""

    @pytest.mark.asyncio
    async def test_execute_query_uses_audit_logger_param(self):
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
        mock_audit = AsyncMock()
        from claude_agent_sdk import AssistantMessage, ToolUseBlock, UserMessage, ToolResultBlock
        tool_use = AssistantMessage(
            content=[ToolUseBlock(id="call_bash", name="bash", input={"command": "ls"})],
            model="test-model",
        )
        tool_result = UserMessage(content=[ToolResultBlock(tool_use_id="call_bash", content="ok")])
        msg = ResultMessage(subtype="result", duration_ms=10, duration_api_ms=5,
                            is_error=False, num_turns=1, session_id="t")
        async def mock_query(*, prompt, options):
            yield tool_use; yield tool_result; yield msg
        with patch("supernova_core.agents.providers_anthropic.query", side_effect=mock_query):
            await provider._execute_query(
                prompt="t", options=ClaudeAgentOptions(model="m", cwd="/tmp"),
                audit_logger=mock_audit,
            )
        mock_audit.log_tool_start.assert_awaited_once_with("bash", {"command": "ls"})
        mock_audit.log_tool_end.assert_awaited_once_with("ok")

    @pytest.mark.asyncio
    async def test_call_forwards_audit_logger(self):
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
        mock_audit = AsyncMock()
        from claude_agent_sdk import AssistantMessage, ToolUseBlock
        tool_use = AssistantMessage(
            content=[ToolUseBlock(id="call_edit", name="edit", input={"path": "a"})],
            model="test-model",
        )
        msg = ResultMessage(subtype="result", duration_ms=10, duration_api_ms=5,
                            is_error=False, num_turns=1, session_id="t")
        async def mock_query(*, prompt, options):
            yield tool_use; yield msg
        with patch("supernova_core.agents.providers_anthropic.query", side_effect=mock_query):
            await provider.call(prompt="t", cwd="/tmp", model_tier="medium", audit_logger=mock_audit)
        mock_audit.log_tool_start.assert_awaited_once_with("edit", {"path": "a"})

    @pytest.mark.asyncio
    async def test_call_without_audit_logger_still_works(self):
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
        msg = ResultMessage(subtype="result", duration_ms=10, duration_api_ms=5,
                            is_error=False, num_turns=1, session_id="t", result="hi")
        async def mock_query(*, prompt, options):
            yield msg
        with patch("supernova_core.agents.providers_anthropic.query", side_effect=mock_query):
            result = await provider.call(prompt="t", cwd="/tmp", model_tier="medium")
        assert result.success is True


class TestRunClaudePromptAuditLogger:
    """L3: run_claude_prompt wraps an ActivityLogger into ActivityToolAuditLogger."""

    @pytest.mark.asyncio
    async def test_wraps_activity_logger(self):
        from supernova_core.logging.activity_logger import ConsoleActivityLogger
        from supernova_core.agents.tool_audit_logger import ActivityToolAuditLogger
        activity_logger = ConsoleActivityLogger()
        mock_provider = AsyncMock()
        mock_provider.call = AsyncMock(return_value=ClaudeRunResult(text="ok", success=True))
        with patch("supernova_core.agents.providers.build_provider_config", return_value=ProviderConfig()):
            with patch("supernova_core.agents.providers.create_provider", return_value=mock_provider):
                await run_claude_prompt(prompt="t", repo_path="/tmp", audit_logger=activity_logger)
        sent = mock_provider.call.call_args.kwargs["audit_logger"]
        assert isinstance(sent, ActivityToolAuditLogger)

    @pytest.mark.asyncio
    async def test_none_audit_logger_passes_none(self):
        mock_provider = AsyncMock()
        mock_provider.call = AsyncMock(return_value=ClaudeRunResult(text="ok", success=True))
        with patch("supernova_core.agents.providers.build_provider_config", return_value=ProviderConfig()):
            with patch("supernova_core.agents.providers.create_provider", return_value=mock_provider):
                await run_claude_prompt(prompt="t", repo_path="/tmp")
        assert mock_provider.call.call_args.kwargs.get("audit_logger") is None


class TestExecutorForwardsAuditLogger:
    """L3: executor.execute forwards audit_logger to run_claude_prompt."""

    @pytest.mark.asyncio
    async def test_forwards_audit_logger(self, tmp_path):
        from supernova_core.agents.executor import AgentExecutor
        from supernova_core.models.agents import AgentName

        repo = tmp_path / "repo"
        repo.mkdir()

        mock_prompt_manager = MagicMock()
        mock_prompt_manager.load_sync.return_value = "prompt body"
        executor = AgentExecutor(mock_prompt_manager)

        sentinel = object()
        with patch("supernova_core.agents.executor.run_claude_prompt",
                   new=AsyncMock(return_value=ClaudeRunResult(text="ok", success=True, turns=3, cost=0.01))) as mock_run, \
             patch("supernova_core.agents.executor.GitManager") as mock_git, \
             patch("supernova_core.agents.executor.validate_deliverable", new=AsyncMock()):
            mock_git.create_checkpoint = AsyncMock()
            mock_git.commit = AsyncMock()
            await executor.execute(
                agent_name=AgentName.RECON,
                repo_path=str(repo),
                deliverables_path=str(repo / "deliverables"),
                audit_logger=sentinel,
            )
        assert mock_run.call_args.kwargs["audit_logger"] is sentinel


class TestBaseProviderContract:
    """D3: 两 provider 都必须是 BaseProvider 的实例（A1 契约硬化锁定）。"""

    def test_anthropic_provider_is_baseprovider_instance(self):
        from supernova_core.agents.providers import BaseProvider
        from supernova_core.agents.providers_anthropic import AnthropicProvider
        from supernova_core.agents.runner import ProviderConfig
        provider = AnthropicProvider(ProviderConfig(type="anthropic_api", api_key="k"))
        assert isinstance(provider, BaseProvider), "AnthropicProvider 必须继承 BaseProvider"

    def test_openai_provider_is_baseprovider_instance(self):
        from supernova_core.agents.providers import BaseProvider
        from supernova_core.agents.providers_openai import OpenAIProvider
        from supernova_core.agents.runner import ProviderConfig
        provider = OpenAIProvider(ProviderConfig(type="openai_compatible", api_key="k"))
        assert isinstance(provider, BaseProvider)

    def test_anthropic_provider_inherits_init_from_base(self):
        """A1: super().__init__ 应设置 config/type，不再手动赋值。"""
        from supernova_core.agents.providers_anthropic import AnthropicProvider
        from supernova_core.agents.runner import ProviderConfig
        cfg = ProviderConfig(type="anthropic_api", api_key="k", base_url="http://x")
        provider = AnthropicProvider(cfg)
        assert provider.config is cfg
        assert provider.type == "anthropic_api"


from supernova_core.models.errors import ErrorCode
from supernova_core.agents.openai_output_schema import StructuredOutputParseError


def _openai_provider():
    return OpenAIProvider(ProviderConfig(
        type="openai_compatible", api_key="test", base_url="https://x.example.com"))


def test_classify_structured_output_parse_error():
    p = _openai_provider()
    code, retryable = p._classify_error(StructuredOutputParseError("bad json"))
    assert code == "OutputValidationError"
    assert retryable is True


def test_handle_error_sets_output_validation_failed_enum():
    p = _openai_provider()
    result = p._handle_error(StructuredOutputParseError("bad json"), 100, "m")
    assert result.error_code == ErrorCode.OUTPUT_VALIDATION_FAILED
    assert result.success is False
    assert result.retryable is True


# ---------------------------------------------------------------------------
# Task 1 (spec-0): BaseProvider.call ABC 签名须含 max_turns（Liskov 补齐）
# 两实现 AnthropicProvider/OpenAIProvider 已有该参数，ABC 补齐签名即可。
# ---------------------------------------------------------------------------
def test_base_provider_call_has_max_turns_parameter():
    """ABC 签名须含 max_turns（两实现已有，补齐 Liskov）。"""
    import inspect
    from supernova_core.agents.providers import BaseProvider

    sig = inspect.signature(BaseProvider.call)
    assert "max_turns" in sig.parameters
    assert sig.parameters["max_turns"].default is None
