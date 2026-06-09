"""
Anthropic Provider 实现

使用 Claude Agent SDK 进行 AI 调用，支持:
- anthropic_api: Anthropic 官方 API
- bedrock: AWS Bedrock
- vertex: Google Cloud Vertex AI
"""

from __future__ import annotations

import logging
import os
import time

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from shannon_core.models.errors import classify_error_for_temporal

from .runner import DEFAULT_MODELS, ClaudeRunResult, ProviderConfig, TokenUsage

logger = logging.getLogger(__name__)


def _on_claude_stderr(line: str) -> None:
    """转发 Claude Code 子进程的真实 stderr，避免 SDK 吞掉错误。

    claude_agent_sdk 仅在注册了回调时才会捕获 CLI 的 stderr；而它的
    ProcessError 会把 stderr 字段替换成占位字符串
    (``"Check stderr output for details"``)，从而掩盖真正的失败原因
    (例如 exit 143 / SIGTERM 的真实触发点)。逐行打日志以恢复可见性。
    """
    logger.warning("[claude-cli stderr] %s", line.rstrip())


class AnthropicProvider:
    """使用 Claude Agent SDK 的 Provider"""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.type = config.type

    def _get_model(self, model_tier: str) -> str:
        """根据 tier 获取模型名称

        优先级: tier-specific override > global model > DEFAULT_MODELS
        """
        # 1. Tier-specific override (最高优先级)
        tier_models = {
            "small": self.config.small_model,
            "medium": self.config.medium_model,
            "large": self.config.large_model,
        }
        tier_model = tier_models.get(model_tier)
        if tier_model:
            return tier_model

        # 2. Global model fallback
        if self.config.model:
            return self.config.model

        # 3. DEFAULT_MODELS (最低优先级)
        provider_key = "anthropic_api"
        if self.type == "bedrock":
            provider_key = "bedrock"
        elif self.type == "vertex":
            provider_key = "vertex"

        models = DEFAULT_MODELS.get(provider_key, DEFAULT_MODELS["anthropic_api"])
        return models.get(model_tier, models.get("medium", "claude-sonnet-4-6"))

    async def call(
        self,
        prompt: str,
        cwd: str,
        model_tier: str = "medium",
        output_format: dict | None = None,
        deliverables_subdir: str | None = None,
    ) -> ClaudeRunResult:
        """
        调用 Claude Agent SDK 执行 prompt

        Args:
            prompt: 用户提示
            cwd: 工作目录
            model_tier: 模型层级
            output_format: 结构化输出格式 (JSON Schema)
            deliverables_subdir: 产物子目录

        Returns:
            ClaudeRunResult: 执行结果
        """
        start_time = time.time()
        model = self._get_model(model_tier)

        try:
            # 构建 SDK 配置
            options = self._build_options(cwd, model, output_format)

            # 执行调用
            result_message = await self._execute_query(prompt, options)

            # 计算耗时
            duration = int((time.time() - start_time) * 1000)

            # 提取结果（使用 dispatcher 的 turn_count）
            turn_count = getattr(result_message, "turn_count", 1)
            result = self._extract_result(result_message, duration, model, turn_count)

            # Layer 1: message-level spending cap detection
            dispatcher_cap_detected = getattr(result_message, "_dispatcher_spending_cap", False)
            if dispatcher_cap_detected:
                result.success = False
                result.retryable = True
                result.error = result.error or "spending cap (message-level detection)"
                return result

            # Layer 2: behavioral spending cap detection
            if self._detect_spending_cap_behavior(result, turn_count):
                result.success = False
                result.retryable = True
                result.error = result.error or "spending cap (behavioral detection)"
                return result

            return result

        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            return self._handle_error(e, duration, model)

    def _build_sdk_env(self) -> dict[str, str]:
        """Build SDK subprocess environment variables (aligned with TS claude-executor.ts)."""
        sdk_env: dict[str, str] = {}

        # Base config
        max_tokens = os.getenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000")
        if max_tokens:
            sdk_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = max_tokens

        # Provider-specific config
        if self.type == "anthropic_api":
            if self.config.api_key:
                sdk_env["ANTHROPIC_API_KEY"] = self.config.api_key
        elif self.type == "bedrock":
            sdk_env["CLAUDE_CODE_USE_BEDROCK"] = "1"
            if self.config.region:
                sdk_env["AWS_REGION"] = self.config.region
        elif self.type == "vertex":
            sdk_env["CLAUDE_CODE_USE_VERTEX"] = "1"
            if self.config.region:
                sdk_env["CLOUD_ML_REGION"] = self.config.region
            if self.config.project_id:
                sdk_env["ANTHROPIC_VERTEX_PROJECT_ID"] = self.config.project_id
        elif self.type == "litellm_router":
            if self.config.base_url:
                sdk_env["ANTHROPIC_BASE_URL"] = self.config.base_url
            if self.config.auth_token:
                sdk_env["ANTHROPIC_AUTH_TOKEN"] = self.config.auth_token

        # Conditional passthrough: inherit from process env if not set above
        PASSTHROUGH_VARS = [
            "ANTHROPIC_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_USE_BEDROCK",
            "AWS_REGION",
            "AWS_BEARER_TOKEN_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLOUD_ML_REGION",
            "ANTHROPIC_VERTEX_PROJECT_ID",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "HOME",
            "PATH",
            "PLAYWRIGHT_MCP_EXECUTABLE_PATH",
        ]

        for var in PASSTHROUGH_VARS:
            if var not in sdk_env:
                val = os.getenv(var)
                if val:
                    sdk_env[var] = val

        return sdk_env

    def _build_options(
        self,
        cwd: str,
        model: str,
        output_format: dict | None = None,
    ) -> ClaudeAgentOptions:
        """构建 ClaudeAgentOptions"""
        options = ClaudeAgentOptions(
            model=model,
            cwd=cwd,
            permission_mode="bypassPermissions",  # 无交互环境必需
        )

        # 添加结构化输出
        if output_format:
            options.output_format = output_format

        # 添加 adaptive thinking
        if self._is_adaptive_thinking_enabled():
            from claude_agent_sdk.types import ThinkingConfigAdaptive
            options.thinking = ThinkingConfigAdaptive(type="adaptive")

        # Environment variables via _build_sdk_env
        options.env = self._build_sdk_env()

        # 捕获 Claude CLI 子进程的真实 stderr。否则 SDK 会丢弃 stderr，
        # 用占位字符串掩盖失败原因。
        options.stderr = _on_claude_stderr

        return options

    def _is_adaptive_thinking_enabled(self) -> bool:
        """检查是否启用 adaptive thinking"""
        env_value = os.getenv("CLAUDE_ADAPTIVE_THINKING", "true").lower()
        return env_value != "false"

    def _detect_spending_cap_behavior(self, result: ClaudeRunResult, turn_count: int) -> bool:
        """Layer 2: behavioral heuristic — low turns + zero cost + no output = suspected cap."""
        if turn_count <= 1 and result.cost == 0.0 and not result.text:
            return True
        return False

    async def _execute_query(
        self,
        prompt: str,
        options: ClaudeAgentOptions,
        dispatcher: MessageDispatcher | None = None,
    ) -> ResultMessage:
        """执行 query 调用并返回最终结果"""
        from .message_dispatcher import MessageDispatcher

        dispatcher = dispatcher or MessageDispatcher()
        final_result: ResultMessage | None = None

        async for event in query(prompt=prompt, options=options):
            action = await dispatcher.dispatch(event)
            if isinstance(event, ResultMessage):
                final_result = event
            if action == "complete":
                break

        if final_result is None:
            final_result = ResultMessage()

        final_result.collected_text = dispatcher.collected_text
        final_result.turn_count = dispatcher.turn_count
        final_result._dispatcher_spending_cap = dispatcher.spending_cap_detected
        return final_result

    def _extract_result(
        self,
        result_message: ResultMessage,
        duration: int,
        model: str,
        turn_count: int = 1,
    ) -> ClaudeRunResult:
        """从 ResultMessage 提取结果"""
        # 提取文本内容
        text = getattr(result_message, "collected_text", "")
        if not text and hasattr(result_message, "result"):
            text = result_message.result or ""

        # 如果有 content 属性，尝试从中提取文本
        if not text and hasattr(result_message, "content"):
            for block in result_message.content:
                if hasattr(block, "text"):
                    text += block.text

        # 提取 token 统计
        tokens = self._extract_tokens(result_message)

        # 提取成本
        cost = self._extract_cost(result_message)

        # 提取结构化输出
        structured_output = None
        if hasattr(result_message, "structured_output") and result_message.structured_output:
            structured_output = result_message.structured_output

        return ClaudeRunResult(
            text=text,
            success=True,
            duration=duration,
            turns=turn_count,
            cost=cost,
            model=model,
            structured_output=structured_output,
            tokens=tokens,
        )

    def _extract_tokens(self, result_message: ResultMessage) -> TokenUsage:
        """从 ResultMessage 提取 token 统计"""
        usage = getattr(result_message, "usage", None)
        if usage is None:
            usage = getattr(result_message, "model_usage", None)

        if usage is None:
            return TokenUsage()

        return TokenUsage(
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0),
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0),
        )

    def _extract_cost(self, result_message: ResultMessage) -> float:
        """从 ResultMessage 提取成本"""
        if hasattr(result_message, "total_cost_usd"):
            return result_message.total_cost_usd or 0.0
        return 0.0

    def _handle_error(
        self,
        error: Exception,
        duration: int,
        model: str,
    ) -> ClaudeRunResult:
        """处理错误 — 使用 classify_error_for_temporal 进行集中式分类"""
        error_msg = str(error)

        # 检查是否是花费上限错误（Layer 3 异常级检测）
        if self._is_spending_cap_error(error_msg):
            return ClaudeRunResult(
                text=error_msg,
                success=False,
                duration=duration,
                turns=0,
                cost=0.0,
                model=model,
                error=f"花费上限: {error_msg}",
                retryable=True,
                error_code="BillingError",
            )

        # 使用集中式错误分类
        error_type, retryable = classify_error_for_temporal(error)

        return ClaudeRunResult(
            text="",
            success=False,
            duration=duration,
            turns=0,
            cost=0.0,
            model=model,
            error=error_msg,
            retryable=retryable,
            error_code=error_type,
        )

    def _is_spending_cap_error(self, error_msg: str) -> bool:
        """检查是否是花费上限错误"""
        keywords = [
            "spending limit",
            "credit limit",
            "quota exceeded",
            "budget exceeded",
            "maximum spend",
        ]
        error_lower = error_msg.lower()
        return any(keyword in error_lower for keyword in keywords)

    def _is_retryable_error(self, error: Exception) -> bool:
        """判断错误是否可重试"""
        error_msg = str(error).lower()
        error_type = type(error).__name__.lower()

        # 速率限制
        if "rate" in error_msg or "limit" in error_msg or error_type == "ratelimiterror":
            return True

        # 超时
        if "timeout" in error_msg or error_type == "timeouterror":
            return True

        # 服务不可用
        if "unavailable" in error_msg or "503" in error_msg or error_type == "serviceunavailable":
            return True

        # 认证错误 - 不可重试
        if "auth" in error_msg or "401" in error_msg or error_type == "authentication":
            return False

        # 权限错误 - 不可重试
        if "permission" in error_msg and "denied" in error_msg:
            return False

        # 默认可重试
        return True
