"""
OpenAI Provider 实现

使用 OpenAI SDK 进行 AI 调用，支持:
- openai_compatible: OpenAI 兼容接口
- litellm_router: LiteLLM 路由器
"""

import os
import time
from typing import Any

from openai import AsyncOpenAI

from .runner import DEFAULT_MODELS, ClaudeRunResult, ProviderConfig, TokenUsage


# OpenAI 模型定价（美元/1K tokens）
# 参考定价，用于成本估算
OPENAI_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "o1": {"input": 0.015, "output": 0.06},
    "o1-mini": {"input": 0.0015, "output": 0.006},
}


class OpenAIProvider:
    """使用 OpenAI SDK 的 Provider"""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.type = config.type
        self._client: AsyncOpenAI | None = None

    def _get_model(self, model_tier: str) -> str:
        """根据 tier 获取模型名称"""
        # 如果配置中指定了模型，优先使用
        if self.config.model:
            return self.config.model

        # 根据类型和 tier 选择默认模型
        if self.type == "litellm_router":
            models = DEFAULT_MODELS.get("litellm_router", DEFAULT_MODELS["anthropic_api"])
        else:
            models = DEFAULT_MODELS.get("openai_compatible", DEFAULT_MODELS["openai_compatible"])

        return models.get(model_tier, models.get("medium", "gpt-4o"))

    def _get_client(self) -> AsyncOpenAI:
        """获取或创建 OpenAI 客户端"""
        if self._client is None:
            client_kwargs: dict[str, Any] = {}

            # API Key
            api_key = self.config.api_key or os.getenv("OPENAI_API_KEY")
            if api_key:
                client_kwargs["api_key"] = api_key

            # Base URL
            if self.config.base_url:
                client_kwargs["base_url"] = self.config.base_url

            # Auth Token (用于 LiteLLM)
            if self.type == "litellm_router" and self.config.auth_token:
                client_kwargs["api_key"] = self.config.auth_token

            self._client = AsyncOpenAI(**client_kwargs)

        return self._client

    async def call(
        self,
        prompt: str,
        cwd: str,
        model_tier: str = "medium",
        output_format: dict | None = None,
        deliverables_subdir: str | None = None,
    ) -> ClaudeRunResult:
        """
        调用 OpenAI API 执行 prompt

        Args:
            prompt: 用户提示
            cwd: 工作目录（OpenAI 不使用，但保持接口一致）
            model_tier: 模型层级
            output_format: 结构化输出格式 (JSON Schema)
            deliverables_subdir: 产物子目录（OpenAI 不使用）

        Returns:
            ClaudeRunResult: 执行结果
        """
        start_time = time.time()
        model = self._get_model(model_tier)

        try:
            client = self._get_client()

            # 构建请求参数
            request_params: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            }

            # 如果提供了 output_format，使用 JSON Mode
            if output_format:
                request_params["response_format"] = {"type": "json_object"}

            # 执行调用
            response = await client.chat.completions.create(**request_params)

            # 计算耗时
            duration = int((time.time() - start_time) * 1000)

            # 提取结果
            return self._extract_result(response, duration, model, output_format is not None)

        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            return self._handle_error(e, duration, model)

    def _extract_result(
        self,
        response: Any,
        duration: int,
        model: str,
        has_json_mode: bool,
    ) -> ClaudeRunResult:
        """从 OpenAI 响应提取结果"""
        # 提取文本内容
        text = ""
        if response.choices:
            text = response.choices[0].message.content or ""

        # 提取 token 统计
        tokens = self._extract_tokens(response)

        # 估算成本
        cost = self._estimate_cost(model, tokens)

        # 提取结构化输出
        structured_output = None
        if has_json_mode and text:
            try:
                import json
                structured_output = json.loads(text)
            except json.JSONDecodeError:
                pass

        # OpenAI 不支持多轮 Agent 调用，固定为 1
        turns = 1

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

    def _extract_tokens(self, response: Any) -> TokenUsage:
        """从 OpenAI 响应提取 token 统计"""
        usage = getattr(response, "usage", None)
        if not usage:
            return TokenUsage()

        return TokenUsage(
            input_tokens=usage.prompt_tokens or 0,
            output_tokens=usage.completion_tokens or 0,
            cache_creation_input_tokens=0,  # OpenAI 不直接暴露
            cache_read_input_tokens=0,  # OpenAI 不直接暴露
        )

    def _estimate_cost(self, model: str, tokens: TokenUsage) -> float:
        """
        根据模型和 token 数量估算成本

        注意：这是估算值，实际成本可能因 Provider 而异
        """
        pricing = OPENAI_PRICING.get(model, OPENAI_PRICING.get("gpt-4o", OPENAI_PRICING["gpt-4o"]))

        input_cost = (tokens.input_tokens / 1000) * pricing["input"]
        output_cost = (tokens.output_tokens / 1000) * pricing["output"]

        return input_cost + output_cost

    def _handle_error(
        self,
        error: Exception,
        duration: int,
        model: str,
    ) -> ClaudeRunResult:
        """处理错误"""
        error_msg = str(error)

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
        if "permission" in error_msg or "403" in error_msg:
            return False

        # 默认可重试
        return True
