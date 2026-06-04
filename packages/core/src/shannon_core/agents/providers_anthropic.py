"""
Anthropic Provider 实现

使用 Claude Agent SDK 进行 AI 调用，支持:
- anthropic_api: Anthropic 官方 API
- bedrock: AWS Bedrock
- vertex: Google Cloud Vertex AI
"""

import os
import time

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from .runner import DEFAULT_MODELS, ClaudeRunResult, ProviderConfig, TokenUsage


class AnthropicProvider:
    """使用 Claude Agent SDK 的 Provider"""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.type = config.type

    def _get_model(self, model_tier: str) -> str:
        """根据 tier 获取模型名称"""
        # 如果配置中指定了模型，优先使用
        if self.config.model:
            return self.config.model

        # 根据类型和 tier 选择默认模型
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

            # 提取结果
            return self._extract_result(result_message, duration, model)

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
            options.thinking = ThinkingConfigAdaptive()

        # Environment variables via _build_sdk_env
        options.env = self._build_sdk_env()

        return options

    def _is_adaptive_thinking_enabled(self) -> bool:
        """检查是否启用 adaptive thinking"""
        env_value = os.getenv("CLAUDE_ADAPTIVE_THINKING", "true").lower()
        return env_value != "false"

    async def _execute_query(
        self,
        prompt: str,
        options: ClaudeAgentOptions,
    ) -> ResultMessage:
        """执行 query 调用并返回最终结果"""
        turns = 0
        final_result: ResultMessage | None = None
        text_content = ""

        async for event in query(prompt=prompt, options=options):
            if isinstance(event, ResultMessage):
                final_result = event
                turns += 1
            elif hasattr(event, "type") and event.type == "text":
                # TextBlock
                text_content += event.text
            elif hasattr(event, "content"):
                # Message with content blocks
                for block in event.content:
                    if hasattr(block, "text"):
                        text_content += block.text

        if final_result is None:
            # 如果没有收到 ResultMessage，创建一个默认的
            final_result = ResultMessage()

        # 将收集的文本内容存储到 result 中
        if text_content and not hasattr(final_result, "collected_text"):
            final_result.collected_text = text_content

        return final_result

    def _extract_result(
        self,
        result_message: ResultMessage,
        duration: int,
        model: str,
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

        # 统计轮次
        turns = 1
        # 查询可能返回多个 result message

        return ClaudeRunResult(
            text=text,
            success=True,
            duration=duration,
            turns=turns,
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
        """处理错误"""
        error_msg = str(error)

        # 检查是否是花费上限错误
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
            )

        # 分类错误
        retryable = self._is_retryable_error(error)

        return ClaudeRunResult(
            text="",
            success=False,
            duration=duration,
            turns=0,
            cost=0.0,
            model=model,
            error=error_msg,
            retryable=retryable,
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
