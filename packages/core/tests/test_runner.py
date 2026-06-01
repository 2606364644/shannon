"""
测试 run_claude_prompt 函数
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shannon_core.agents.runner import (
    ClaudeRunResult,
    ProviderConfig,
    TokenUsage,
    run_claude_prompt,
)


class TestClaudeRunResult:
    """测试 ClaudeRunResult 数据类"""

    def test_claude_run_result_defaults(self):
        """测试默认值"""
        result = ClaudeRunResult()
        assert result.text == ""
        assert result.success is False
        assert result.duration == 0
        assert result.turns == 0
        assert result.cost == 0.0
        assert result.model is None
        assert result.structured_output is None
        assert result.error is None
        assert result.retryable is True
        assert isinstance(result.tokens, TokenUsage)

    def test_claude_run_result_with_values(self):
        """测试完整值"""
        tokens = TokenUsage(input_tokens=100, output_tokens=50)
        result = ClaudeRunResult(
            text="hello",
            success=True,
            duration=5000,
            turns=3,
            cost=0.05,
            model="claude-sonnet-4-6",
            structured_output={"key": "value"},
            error=None,
            retryable=False,
            tokens=tokens,
        )
        assert result.text == "hello"
        assert result.success is True
        assert result.cost == 0.05
        assert result.structured_output == {"key": "value"}
        assert result.tokens.input_tokens == 100
        assert result.tokens.output_tokens == 50


class TestRunClaudePrompt:
    """测试 run_claude_prompt 函数"""

    @pytest.mark.asyncio
    async def test_run_claude_prompt_success(self):
        """测试成功调用"""
        mock_provider = MagicMock()
        mock_result = ClaudeRunResult(
            text="Test response",
            success=True,
            duration=1000,
            turns=1,
            cost=0.001,
            model="claude-sonnet-4-6",
            tokens=TokenUsage(input_tokens=100, output_tokens=50),
        )
        mock_provider.call = AsyncMock(return_value=mock_result)

        with patch("shannon_core.agents.providers.create_provider", return_value=mock_provider):
            result = await run_claude_prompt(
                prompt="Test prompt",
                repo_path="/tmp/test",
            )

        assert result.success is True
        assert result.text == "Test response"
        assert result.tokens.input_tokens == 100

    @pytest.mark.asyncio
    async def test_run_claude_prompt_with_provider_config(self):
        """测试使用 provider_config 参数"""
        mock_provider = MagicMock()
        mock_result = ClaudeRunResult(success=True, text="OK")
        mock_provider.call = AsyncMock(return_value=mock_result)

        provider_config = {
            "type": "openai_compatible",
            "api_key": "test-key",
            "base_url": "https://api.example.com",
        }

        with patch("shannon_core.agents.providers.create_provider", return_value=mock_provider) as mock_create:
            await run_claude_prompt(
                prompt="Test",
                repo_path="/tmp",
                provider_config=provider_config,
            )

            # 验证使用了传入的配置
            created_config = mock_create.call_args[0][0]
            assert created_config.type == "openai_compatible"
            assert created_config.api_key == "test-key"

    @pytest.mark.asyncio
    async def test_run_claude_prompt_structured_output_schema_alias(self):
        """测试 structured_output_schema 别名"""
        mock_provider = MagicMock()
        mock_result = ClaudeRunResult(success=True, text="OK")
        mock_provider.call = AsyncMock(return_value=mock_result)

        with patch("shannon_core.agents.providers.create_provider", return_value=mock_provider):
            await run_claude_prompt(
                prompt="Test",
                repo_path="/tmp",
                structured_output_schema={"type": "object"},
            )

            # 验证 output_format 被传递
            call_kwargs = mock_provider.call.call_args[1]
            assert call_kwargs["output_format"] == {"type": "object"}

    @pytest.mark.asyncio
    async def test_run_claude_prompt_spending_cap_detection(self):
        """测试花费上限检测"""
        mock_provider = MagicMock()
        mock_result = ClaudeRunResult(
            success=False,
            error="spending limit reached",
            turns=1,
            cost=0.0,
        )
        mock_provider.call = AsyncMock(return_value=mock_result)

        with patch("shannon_core.agents.providers.create_provider", return_value=mock_provider):
            result = await run_claude_prompt(
                prompt="Test",
                repo_path="/tmp",
            )

        assert result.success is False
        assert result.retryable is True
        assert "spending" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_run_claude_prompt_exception_handling(self):
        """测试异常处理"""
        mock_provider = MagicMock()
        mock_provider.call = AsyncMock(side_effect=Exception("Connection failed"))

        with patch("shannon_core.agents.providers.create_provider", return_value=mock_provider):
            result = await run_claude_prompt(
                prompt="Test",
                repo_path="/tmp",
            )

        assert result.success is False
        assert "未处理的异常" in (result.error or "")
        assert result.retryable is True

    def test_run_claude_prompt_accepts_structured_output_schema(self):
        """验证 run_claude_prompt 签名接受 structured_output_schema 参数"""
        import inspect
        sig = inspect.signature(run_claude_prompt)
        assert "structured_output_schema" in sig.parameters
        assert sig.parameters["structured_output_schema"].default is None


class TestTokenUsage:
    """测试 TokenUsage 数据类"""

    def test_token_usage_defaults(self):
        """测试默认值"""
        tokens = TokenUsage()
        assert tokens.input_tokens == 0
        assert tokens.output_tokens == 0
        assert tokens.cache_creation_input_tokens == 0
        assert tokens.cache_read_input_tokens == 0

    def test_token_usage_total(self):
        """测试 total_tokens 属性"""
        tokens = TokenUsage(input_tokens=1000, output_tokens=500)
        assert tokens.total_tokens == 1500

    def test_token_usage_with_cache(self):
        """测试包含缓存的统计"""
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


class TestProviderConfig:
    """测试 ProviderConfig 数据类"""

    def test_provider_config_defaults(self):
        """测试默认配置"""
        config = ProviderConfig()
        assert config.type == "anthropic_api"
        assert config.api_key is None
        assert config.base_url is None

    def test_provider_config_full(self):
        """测试完整配置"""
        config = ProviderConfig(
            type="openai_compatible",
            api_key="test-key",
            base_url="https://api.example.com",
            model="gpt-4o",
            region="us-west-2",
            project_id="test-project",
            auth_token="auth-token",
        )
        assert config.type == "openai_compatible"
        assert config.api_key == "test-key"
        assert config.base_url == "https://api.example.com"
        assert config.model == "gpt-4o"
        assert config.region == "us-west-2"
        assert config.project_id == "test-project"
        assert config.auth_token == "auth-token"


class TestIsSpendingCapBehavior:
    """测试花费上限检测"""

    def test_is_spending_cap_behavior_true(self):
        """测试识别花费上限行为"""
        from shannon_core.agents.runner import _is_spending_cap_behavior

        result = ClaudeRunResult(
            success=False,
            error="spending limit reached",
        )
        assert _is_spending_cap_behavior(result) is True

    def test_is_spending_cap_behavior_variations(self):
        """测试各种花费上限关键词"""
        from shannon_core.agents.runner import _is_spending_cap_behavior

        keywords = [
            "credit limit exceeded",
            "quota exceeded",
            "budget exceeded",
            "maximum spend reached",
        ]

        for error_msg in keywords:
            result = ClaudeRunResult(
                success=False,
                error=error_msg,
            )
            assert _is_spending_cap_behavior(result) is True, f"Failed for: {error_msg}"

    def test_is_spending_cap_behavior_false(self):
        """测试非花费上限错误"""
        from shannon_core.agents.runner import _is_spending_cap_behavior

        result = ClaudeRunResult(
            success=True,
            error=None,
        )
        assert _is_spending_cap_behavior(result) is False

        result = ClaudeRunResult(
            success=False,
            error="rate limit exceeded",
        )
        assert _is_spending_cap_behavior(result) is False
