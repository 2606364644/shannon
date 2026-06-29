"""OpenAI Provider（基于 openai-agents，Chat Completions 模式接第三方 OpenAI 兼容接口）。

设计见 docs/superpowers/specs/2026-06-17-openai-agents-engine-design.md。
与 AnthropicProvider 双引擎并存，经 SHANNON_AI_PROVIDER=openai_compatible 切换。
"""
from __future__ import annotations

import json
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

from .narration import narration_directive
from .openai_output_schema import (
    RawJsonSchemaOutputSchema,
    StructuredOutputParseError,
    _extract_json_payload,
)
from shannon_core.models.errors import ErrorCode
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

    def _subagent_max_turns(self) -> int:
        # 子代理（Task 委派）max_turns。结构层已硬限单层（子代理无 subagent_run
        # + 只读工具集 [read_file, glob, grep]），调大无递归风险，仅增单次 token。
        # B2: 20→40,锚定更复杂的追链子任务。
        return int(os.getenv("SHANNON_OPENAI_SUBAGENT_MAX_TURNS", "40"))

    def _instructions(self) -> str | None:
        """Part B: narration-language directive as the parent Agent's system message.

        None when disabled → unchanged behavior (prompt passed as user input, as
        before). Only the parent agent gets the directive; the task subagent
        (`_make_subagent_runner`) intentionally does NOT — it returns terse
        code-reading data consumed by the parent, and injecting the directive
        there would risk Chinese leaking into that data.
        """
        return narration_directive()

    def build_agent(self, model: str, output_format: dict | None) -> Agent:
        client = self._get_client()
        chat_model = OpenAIChatCompletionsModel(model=model, openai_client=client)
        # B2: output_format 非空时强制结构化输出（对齐 Claude options.output_format）
        output_type = RawJsonSchemaOutputSchema(output_format) if output_format else None
        return Agent(
            name="shannon-openai-agent",
            instructions=self._instructions(),  # None when disabled
            tools=build_tools(),
            model=chat_model,
            model_settings=ModelSettings(include_usage=True),
            output_type=output_type,
        )

    def _make_subagent_runner(self, model: str, cwd: str):
        """构建子代理 runner：代码阅读 Agent（read/glob/grep）跑 prompt，返回 final_output。

        spec 改动 4a：对齐 Claude Code CLI 的 Task tool —— 父 agent 调用 `task`
        function_tool 时，spawn 一个只读代码的子代理（read_file/glob/grep，无 bash/write/task），
        跑完返回其 final_output，保持父上下文精简。

        子代理 ToolContext 不注入 subagent_run（防嵌套递归）。
        """
        from .tools_openai.exec import grep
        from .tools_openai.fs import glob, read_file

        client = self._get_client()
        chat_model = OpenAIChatCompletionsModel(model=model, openai_client=client)
        subagent = Agent(
            name="shannon-task-subagent",
            instructions=None,  # prompt 当 user input
            tools=[read_file, glob, grep],
            model=chat_model,
            model_settings=ModelSettings(include_usage=False),
        )
        max_turns = self._subagent_max_turns()

        async def run(prompt: str) -> str:
            res = await Runner.run(
                subagent,
                input=prompt,
                context=ToolContext(cwd=cwd),  # 子代理同 cwd，无 subagent_run（防递归）
                max_turns=max_turns,
            )
            return str(res.final_output)

        return run

    async def _lightweight_reparse(self, text: str, output_format: dict | None, model: str):
        """L1：L0 容错失败后，发单个轻量 chat completion 让 GLM 把分析转纯 JSON。

        模拟 Claude SDK 单次内部重试（openai-agents 无此层）。仅 1 个 chat completion，
        无 agent loop / 工具 / narration directive。不传 response_format（GLM 第三方后端
        兼容不确定），靠 prompt + _extract_json_payload 兜底。任一步失败 → None（进 L2）。
        """
        if not output_format or not text or not text.strip():
            return None
        client = self._get_client()
        prompt = (
            "将以下分析结论转为符合 schema 的纯 JSON，只输出 JSON 本体，"
            "不要任何解释、前言或 markdown 代码围栏：\n" + text
        )
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception:
            return None
        choices = getattr(resp, "choices", None)
        content = ""
        if choices:
            content = getattr(choices[0].message, "content", "") or ""
        candidate = _extract_json_payload(content)
        if candidate is None:
            return None
        try:
            recovered = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            return None
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
        out_tok = getattr(usage, "completion_tokens", 0) or 0
        return _ReparsedRunResult(recovered, in_tok, out_tok)

    async def call(
        self,
        prompt: str,
        cwd: str,
        model_tier: str = "medium",
        output_format: dict | None = None,
        deliverables_subdir: str | None = None,
        audit_logger: ToolAuditLogger | None = None,
        max_turns: int | None = None,
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
                    context=ToolContext(cwd=cwd, subagent_run=self._make_subagent_runner(model, cwd)),
                    max_turns=max_turns or self._max_turns(),
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
        error_code, retryable = self._classify_error(error)
        # StructuredOutputParseError 走 ErrorCode enum（供 executor isinstance 守卫透传 →
        # classify_error_for_temporal Level 1 匹配 OUTPUT_VALIDATION_FAILED）；其他错误
        # 保留 _classify_error 的字符串（Temporal error type，executor 不透传，保持
        # AGENT_EXECUTION_FAILED 现有行为，避免破坏 RateLimit/Timeout 分类）。
        if isinstance(error, StructuredOutputParseError):
            error_code = ErrorCode.OUTPUT_VALIDATION_FAILED
        return ClaudeRunResult(
            text="",
            success=False,
            duration=duration,
            turns=0,
            cost=0.0,
            model=model,
            error=str(error),
            error_code=error_code,
            retryable=retryable,
        )

    def _classify_error(self, error: Exception) -> tuple[str | None, bool]:
        """分类异常 → (error_code, retryable)。retryable 与 models/errors.py:classify_error_for_temporal
        真值对齐；error_code 字符串走 Pre-Flight 分类表（语义化，允许两引擎差异，见 spec §1.4）。

        BaseProvider._is_retryable_error 只匹配自定义异常类；openai/httpx/agents
        抛的是普通异常，需基于消息和类型名分类。
        """
        if isinstance(error, StructuredOutputParseError):
            return ("OutputValidationError", True)
        error_msg = str(error).lower()
        error_type = type(error).__name__.lower()
        # 速率限制 → 可重试
        if "rate" in error_msg or "limit" in error_msg or error_type == "ratelimiterror":
            return ("RateLimitError", True)
        # 超时 → 可重试
        if "timeout" in error_msg or error_type in ("timeouterror", "timeoutexception", "connecttimeout"):
            return ("TimeoutError", True)
        # 服务不可用 → 可重试
        if "unavailable" in error_msg or "503" in error_msg or "502" in error_msg or "504" in error_msg or error_type == "serviceunavailable":
            return ("ServiceUnavailableError", True)
        # 认证 → 不可重试
        if "auth" in error_msg or "401" in error_msg or error_type == "authenticationerror":
            return ("AuthenticationError", False)
        # 权限 → 不可重试
        if "permission" in error_msg or "403" in error_msg or error_type == "permissiondeniederror":
            return ("PermissionError", False)
        # 默认可重试（与旧行为一致）
        return ("AgentExecutionError", True)

    def _is_retryable_error(self, error: Exception) -> bool:
        """判断错误是否可重试（BaseProvider 契约，委托 _classify_error，DRY）。"""
        return self._classify_error(error)[1]


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


class _ReparsedRunResult:
    """L1 轻量重输成功后的最小 RunResult stub。

    仅含 map_run_result 需要的 final_output（= recovered dict）+ context_wrapper.usage
    （带 L1 chat completion 的真实 token，避免统计失真；cost 仍走 GLM 0.0 早退）。
    usage 用普通类承载（不用 MagicMock），避免 map_run_result 的
    getattr(usage, "input_tokens", 0) 被 MagicMock 恒真干扰。
    """
    def __init__(self, final_output, input_tokens: int = 0, output_tokens: int = 0):
        self.final_output = final_output

        class _U:
            def __init__(self):
                self.input_tokens = input_tokens
                self.output_tokens = output_tokens

        class _CW:
            def __init__(self):
                self.usage = _U()

        self.context_wrapper = _CW()
