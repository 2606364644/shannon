"""OpenAI Provider（基于 openai-agents，Chat Completions 模式接第三方 OpenAI 兼容接口）。

设计见 docs/superpowers/specs/2026-06-17-openai-agents-engine-design.md。
与 AnthropicProvider 双引擎并存，经 SHANNON_AI_PROVIDER=openai_compatible 切换。
"""
from __future__ import annotations

import os
import time

from agents import (
    Agent,
    MaxTurnsExceeded,
    ModelSettings,
    OpenAIChatCompletionsModel,
    Runner,
    RunContextWrapper,
    set_tracing_disabled,
)
from openai import AsyncOpenAI

from .openai_result_mapper import map_run_result
from .openai_stream_collector import StreamCollector
from .providers import BaseProvider, ProviderConfig
from .runner import DEFAULT_MODELS, ClaudeRunResult, TokenUsage
from .tool_audit_logger import ToolAuditLogger
from .tools_openai import ToolContext, build_tools

_tracing_disabled = False


class OpenAIProvider(BaseProvider):
    """使用 openai-agents 的 Provider（多轮 tool use agent loop）。"""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        global _tracing_disabled
        if not _tracing_disabled:
            set_tracing_disabled(True)  # 第三方 base_url，关掉 trace 上传避免 401
            _tracing_disabled = True
        self._client: AsyncOpenAI | None = None

    # —— 模型解析（沿用现有语义）——
    def _get_model(self, model_tier: str) -> str:
        tier_models = {
            "small": self.config.small_model,
            "medium": self.config.medium_model,
            "large": self.config.large_model,
        }
        if tier_models.get(model_tier):
            return tier_models[model_tier]
        if self.config.model:
            return self.config.model
        key = "litellm_router" if self.type == "litellm_router" else "openai_compatible"
        models = DEFAULT_MODELS.get(key, DEFAULT_MODELS["openai_compatible"])
        return models.get(model_tier, models.get("medium", "gpt-4o"))

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            kwargs: dict = {}
            api_key = self.config.api_key or os.getenv("OPENAI_API_KEY")
            if api_key:
                kwargs["api_key"] = api_key
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            if self.type == "litellm_router" and self.config.auth_token:
                kwargs["api_key"] = self.config.auth_token
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    def _max_turns(self) -> int:
        return int(os.getenv("SHANNON_OPENAI_MAX_TURNS", "200"))

    def build_agent(self, model: str, output_format: dict | None) -> Agent:
        client = self._get_client()
        chat_model = OpenAIChatCompletionsModel(model=model, openai_client=client)
        return Agent(
            name="shannon-openai-agent",
            instructions=None,  # prompt 已含 system prompt，整段当 user input
            tools=build_tools(),
            model=chat_model,
            model_settings=ModelSettings(include_usage=True),
        )

    async def call(
        self,
        prompt: str,
        cwd: str,
        model_tier: str = "medium",
        output_format: dict | None = None,
        deliverables_subdir: str | None = None,
        audit_logger: ToolAuditLogger | None = None,
    ) -> ClaudeRunResult:
        start_time = time.time()
        model = self._get_model(model_tier)
        try:
            agent = self.build_agent(model, output_format)
            collector = StreamCollector(audit_logger)
            stop_reason: str | None = None
            try:
                result = Runner.run_streamed(
                    agent,
                    input=prompt,
                    context=ToolContext(cwd=cwd),
                    max_turns=self._max_turns(),
                )
                async for event in result.stream_events():
                    await collector.on_event(event)
                await collector.close()
                run_result = result
            except MaxTurnsExceeded:
                await collector.close()
                # 无可用 RunResult，构造一个最小结果对象
                run_result = _MaxTurnsStub(collector.text)
                stop_reason = "max_turns"

            duration = int((time.time() - start_time) * 1000)
            return map_run_result(
                run_result,
                duration_ms=duration,
                model=model,
                turns=max(collector.turns, 1),
                stop_reason=stop_reason,
                output_format=output_format,
            )
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            return self._handle_error(e, duration, model)

    def _handle_error(self, error: Exception, duration: int, model: str) -> ClaudeRunResult:
        return ClaudeRunResult(
            text="",
            success=False,
            duration=duration,
            turns=0,
            cost=0.0,
            model=model,
            error=str(error),
            retryable=self._is_retryable_error(error),
        )

    def _is_retryable_error(self, error: Exception) -> bool:
        """判断错误是否可重试。

        BaseProvider 的实现只匹配自定义异常类；openai/httpx/agents 抛的是普通异常，
        需基于消息和类型名分类，对齐旧 OpenAIProvider / AnthropicProvider 行为。
        """
        error_msg = str(error).lower()
        error_type = type(error).__name__.lower()
        # 速率限制 / 超时 / 服务不可用 → 可重试
        if "rate" in error_msg or "limit" in error_msg or error_type == "ratelimiterror":
            return True
        if "timeout" in error_msg or error_type in ("timeouterror", "timeoutexception", "connecttimeout"):
            return True
        if "unavailable" in error_msg or "503" in error_msg or "502" in error_msg or "504" in error_msg or error_type == "serviceunavailable":
            return True
        # 认证 / 权限 → 不可重试
        if "auth" in error_msg or "401" in error_msg or error_type == "authenticationerror":
            return False
        if "permission" in error_msg or "403" in error_msg or error_type == "permissiondeniederror":
            return False
        # 默认可重试（与旧行为一致）
        return True


class _MaxTurnsStub:
    """MaxTurnsExceeded 时无 RunResult，伪造一个只含 final_output 的对象供 map_run_result 使用。"""

    def __init__(self, text: str):
        class _CW:
            class _U:
                input_tokens = 0
                output_tokens = 0
            usage = _U()
        self.final_output = text
        self.context_wrapper = _CW()
