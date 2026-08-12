"""OpenAI Provider（基于 openai-agents，Chat Completions 模式接第三方 OpenAI 兼容接口）。

设计见 docs/superpowers/specs/2026-06-17-openai-agents-engine-design.md。
与 AnthropicProvider 双引擎并存，经 SUPERNOVA_AI_PROVIDER=openai_compatible 切换。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, TYPE_CHECKING

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
from .llm_json import repair_json_arguments
from .openai_output_schema import (
    StructuredOutputParseError,
    _extract_json_payload,
)
from supernova_core.models.errors import ErrorCode
from .openai_result_mapper import map_run_result
from .openai_stream_collector import StreamCollector
from .providers import BaseProvider, ProviderConfig
from .runner import ClaudeRunResult, TokenUsage
from .tool_audit_logger import ToolAuditLogger
from .tools_openai import ToolContext, build_tools

if TYPE_CHECKING:
    from supernova_core.agents.progress_tool import ProgressSpec
    from supernova_core.collectors.base import CollectorBase

_tracing_disabled = False


class _AttrProxy:
    """透传 wrapper：``__getattr__`` 转发到 target，仅覆盖 overrides 中的属性。

    用于包装 ``AsyncOpenAI`` client：透传所有属性（``base_url`` / ``models`` / ...），
    仅拦截 ``chat.completions.create`` 在发包前清洗 tool_call 非法 arguments。
    ``__getattr__`` 全透传，避免漏转发破坏 SDK（trace 取 ``base_url`` 等）。
    """

    def __init__(self, target: Any, overrides: dict[str, Any]):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_overrides", overrides)

    def __getattr__(self, name: str) -> Any:
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._target, name)


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """原地清洗 messages：把 assistant tool_call 的非法 JSON arguments 修好或兜底 ``"{}"``。

    第三方 openai 兼容端点（火山方舟 ARK 等）消费侧校验 ``tool_call.function.arguments``
    必须是合法 JSON；GLM 偶发吐残缺/markdown 串，openai-agents 的 Chat Completions 模式
    无状态全量重发，会把它原样塞回 history 再次发送 → 端点 400 ``Invalid request body``。
    本函数在请求发出前把非法串修好（复用 ``repair_json_arguments``），修不好兜底 ``"{}"``
    （探针确认 ARK 接受 ``{}``）止血 400。合法 arguments 与无 tool_calls 的消息原样不动。

    与防线1（``bridge._on_invoke_set`` 在工具层让模型重发）互补：这里只管发包不 400，
    不负责语义对错（参考 "Your LLM JSON Is Valid — And Still Wrong"）。
    """
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            if not isinstance(fn, dict):
                continue
            args = fn.get("arguments")
            if not isinstance(args, str) or not args.strip():
                continue
            repaired = repair_json_arguments(args)
            fn["arguments"] = repaired if repaired is not None else "{}"
    return messages


def _wrap_client_for_argument_sanitize(client: AsyncOpenAI) -> Any:
    """返回 client 的代理：透传所有属性，仅 ``chat.completions.create`` 发包前清洗 messages。"""
    original_create = client.chat.completions.create

    async def _sanitized_create(*args: Any, **kwargs: Any) -> Any:
        messages = kwargs.get("messages")
        if messages is not None:
            kwargs["messages"] = _sanitize_messages(messages)
        return await original_create(*args, **kwargs)

    completions_proxy = _AttrProxy(client.chat.completions, {"create": _sanitized_create})
    chat_proxy = _AttrProxy(client.chat, {"completions": completions_proxy})
    return _AttrProxy(client, {"chat": chat_proxy})


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
        from .providers import resolve_tier_model
        return resolve_tier_model(self.config, model_tier)

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
            # L4: HTTP 层超时/重试熔断（区别于 _call_timeout 的整 stream wall-clock 兜底）。
            # openai 引擎是 in-process SDK，缺 claude CLI 子进程那层 HTTP 超时兜底（CLAUDE.md §2）；
            # AsyncOpenAI 默认 600s/2 次，单次 /chat/completions 请求 stall 时 SDK 内部按 600s 等待
            # + 重试放大、worker 线程长 sleeping。env 驱动保守默认 300s/1：单次请求熔断 + 限重试，
            # stall 时更快抛 timeout -> _classify_error 判 retryable -> activity 重试，不静默 hang。
            # （非本次 OOM 真根因——OOM 是内存；本加固防 stall 类 hang，防御性。）
            kwargs["timeout"] = float(os.getenv("SUPERNOVA_OPENAI_HTTP_TIMEOUT", "300"))
            kwargs["max_retries"] = int(os.getenv("SUPERNOVA_OPENAI_MAX_RETRIES", "3"))
            self._client = _wrap_client_for_argument_sanitize(AsyncOpenAI(**kwargs))
        return self._client

    def _max_turns(self) -> int:
        # 默认 10000 对齐原始 TS shannon（claude-executor.ts: maxTurns:10_000）：仅作 runaway
        # 硬兜底，成本兜底靠 spending cap（utils/billing，同 TS）。
        # P3c 阶段 0：self.config.max_turns 优先（None 回落 env）
        if self.config.max_turns is not None:
            return self.config.max_turns
        return int(os.getenv("SUPERNOVA_OPENAI_MAX_TURNS", "10000"))

    def _subagent_max_turns(self) -> int:
        # 子代理（Task 委派）max_turns。结构层已硬限单层（子代理无 subagent_run
        # + 只读工具集 [read_file, glob, grep]），调大无递归风险，仅增单次 token。
        # B2: 20→40,锚定更复杂的追链子任务；后续 40→100 给追第三方包源码等更长子任务。
        # P3c 阶段 0：self.config.subagent_max_turns 优先（None 回落 env）
        if self.config.subagent_max_turns is not None:
            return self.config.subagent_max_turns
        return int(os.getenv("SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS", "100"))

    def _call_timeout(self) -> float:
        """call() stream 消费的 wall-clock 超时（秒）—— openai 引擎自补的超时兜底。

        claude 引擎经 CLI 子进程，子进程内置 HTTP 超时/retry + 崩溃即退出（activity 感知）；
        openai 引擎是 in-process SDK，缺这层运行时（CLAUDE.md §2「CLI 运行时 vs 纯框架」）。
        deepseek 流式响应 stall（服务端/网络断流，TCP 未断）或 SDK 内部 await 卡住时，
        stream_events 永久 await、worker 静默 hang（2026-07-16 trip_1784167551：pre-recon
        卡死 50min，2h activity timeout 未到，live 页无日志更新）。超时 → asyncio.TimeoutError
        → 外层 except → _classify_error 判 retryable → activity 重试。默认 2400s（40min，
        覆盖 pre-recon 等长 agent 的正常时长，仅兜底永久 hang，不误杀慢但正常的 run）。

        P3c 阶段 0：self.config.call_timeout 优先（None 回落 env，默认 2400s）。
        """
        if self.config.call_timeout is not None:
            return self.config.call_timeout
        return float(os.getenv("SUPERNOVA_OPENAI_CALL_TIMEOUT", "2400"))

    def _instructions(self) -> str | None:
        """Part B: narration-language directive as the parent Agent's system message.

        None when disabled → unchanged behavior (prompt passed as user input, as
        before). Only the parent agent gets the directive; the task subagent
        (`_make_subagent_runner`) intentionally does NOT — it returns terse
        code-reading data consumed by the parent, and injecting the directive
        there would risk Chinese leaking into that data.
        """
        return narration_directive()

    def build_agent(self, model: str, output_format: dict | None, extra_tools: list | None = None) -> Agent:
        client = self._get_client()
        chat_model = OpenAIChatCompletionsModel(model=model, openai_client=client)
        # 不设 output_type：第三方 openai 兼容端点（deepseek/GLM）不支持
        # response_format.type=json_schema，传之必 400 "This response_format type is
        # unavailable now"（探针确证 2026-07-24，致 injection/authz/auth/ssrf 四个
        # *-vuln agent 8/8 重试全失败）。openai-agents 的 convert_response_format 只要
        # output_type 非 None 且 is_plain_text()=False 就向端点传 json_schema（无论
        # strict）。故结构化输出改为 prompt 约束 + map_run_result 本地 L0 解析
        # （_extract_json_payload）+ call L1 轻量重输兜底。属"功能性对齐"（同 prompt+
        # schema 产出 structured_output），机制差异是 SDK 哲学（CLAUDE.md §2）；claude
        # 引擎仍走 CLI --json-schema（端点支持）。output_format 参数保留供
        # map_run_result/call 触发本地解析，不传给 SDK。
        return Agent(
            name="shannon-openai-agent",
            instructions=self._instructions(),  # None when disabled
            tools=build_tools() + (extra_tools or []),
            model=chat_model,
            model_settings=ModelSettings(include_usage=True),
            output_type=None,
        )

    def _make_subagent_runner(self, model: str, cwd: str, proxy_url: str | None = None):
        """构建子代理 runner：代码阅读 Agent（read/glob/grep）跑 prompt，返回 final_output。

        spec 改动 4a：对齐 Claude Code CLI 的 Task tool —— 父 agent 调用 `task`
        function_tool 时，spawn 一个只读代码的子代理（read_file/glob/grep，无 bash/write/task），
        跑完返回其 final_output，保持父上下文精简。

        子代理 ToolContext 不注入 subagent_run（防嵌套递归）。
        Task 4: ``proxy_url`` 透传给子代理 ToolContext（与主 agent 同一 per-scan 代理；
        子代理工具集仅 read/glob/grep 当前不读 proxy_url，但保持对称注入供未来扩展）。
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
                context=ToolContext(cwd=cwd, proxy_url=proxy_url),  # 子代理同 cwd，无 subagent_run（防递归）
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
        collector: "CollectorBase | None" = None,
        progress: "ProgressSpec | None" = None,
        proxy_url: str | None = None,   # Task 4：per-scan 代理穿线 → ToolContext（Task 2 工具读此字段）
    ) -> ClaudeRunResult:
        start_time = time.time()
        model = self._get_model(model_tier)
        streaming = None  # SDK RunResultStreaming，供 error path（_handle_error/MaxTurnsExceeded）提累积 usage
        try:
            # collector + progress → 本引擎原生工具（FunctionTool list）。
            # engine-agnostic：caller 只传 CollectorBase/ProgressSpec，provider 经 compose 组装。
            from supernova_core.agents.progress_tool import compose_openai_extra_tools
            extra_tools = compose_openai_extra_tools(collector, progress) or None
            agent = self.build_agent(model, output_format, extra_tools=extra_tools)
            # stream_collector 收 openai stream events（与 CollectorBase 无关，避免与
            # 上面的 collector 参数命名冲突）。
            stream_collector = StreamCollector(audit_logger)
            stop_reason: str | None = None
            try:
                result = Runner.run_streamed(
                    agent,
                    input=prompt,
                    context=ToolContext(
                        cwd=cwd,
                        subagent_run=self._make_subagent_runner(model, cwd, proxy_url),
                        proxy_url=proxy_url,
                    ),
                    max_turns=max_turns or self._max_turns(),
                )
                streaming = result  # alias 供 error path 提累积 usage（run_streamed 已返回）

                async def _consume_stream() -> None:
                    async for event in result.stream_events():
                        await stream_collector.on_event(event)

                # wall-clock 兜底：openai 引擎 in-process，缺 claude CLI 子进程的 HTTP 超时
                # 兜底；deepseek 流式 stall / SDK 内部 await 卡住时 stream_events 永久 await、
                # worker 静默 hang（2026-07-16 trip_1784167551）。超时 raise asyncio.TimeoutError
                # → 外层 except → _classify_error 判 retryable → activity 重试，不再静默卡死。
                await asyncio.wait_for(_consume_stream(), timeout=self._call_timeout())
                await stream_collector.close()
                run_result = result
            except MaxTurnsExceeded:
                await stream_collector.close()
                # result（run_streamed 返回）的 context_wrapper.usage 已累积到超轮数的 token
                # （SDK 无清零），用它算 cost（弃旧 _MaxTurnsStub 硬编码 0 usage）。
                mt_usage = streaming.context_wrapper.usage if streaming is not None else None
                run_result = _MaxTurnsStub(stream_collector.text, mt_usage)
                stop_reason = "max_turns"

            duration = int((time.time() - start_time) * 1000)
            result = map_run_result(
                run_result,
                duration_ms=duration,
                model=model,
                turns=max(stream_collector.turns, 1),
                stop_reason=stop_reason,
                output_format=output_format,
            )
            # L1：openai 引擎不设 output_type -> SDK 不调 validate_json，L0 容错解析在
            # map_run_result 完成；L0 失败（final 非法 JSON -> structured_output is None）
            # 时轻量重输兜底，模拟 Claude SDK 单次内部重试（openai-agents 无此层）。
            # L1 成功 -> 用 _ReparsedRunResult（含 L1 chat completion 真实 token usage）
            # 重走 map_run_result，L1 token 计入 cost（与原 StructuredOutputParseError
            # 路径语义一致）。L1 失败 / 未触发 -> 返回原 result（structured_output=None，
            # caller 自行降级）。
            if output_format and result.success and result.structured_output is None:
                final_text = stream_collector.text or ""
                if final_text.strip():
                    reparsed = await self._lightweight_reparse(final_text, output_format, model)
                    if reparsed is not None:
                        return map_run_result(
                            reparsed,
                            duration_ms=duration,
                            model=model,
                            turns=max(stream_collector.turns, 1),
                            stop_reason=stop_reason,
                            output_format=output_format,
                        )
            return result
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            return self._handle_error(e, duration, model, run_result=streaming)

    def _handle_error(
        self, error: Exception, duration: int, model: str,
        run_result: Any = None,
    ) -> ClaudeRunResult:
        error_code, retryable = self._classify_error(error)
        # StructuredOutputParseError 走 ErrorCode enum（供 executor isinstance 守卫透传 →
        # classify_error_for_temporal Level 1 匹配 OUTPUT_VALIDATION_FAILED）；其他错误
        # 保留 _classify_error 的字符串（Temporal error type，executor 不透传，保持
        # AGENT_EXECUTION_FAILED 现有行为，避免破坏 RateLimit/Timeout 分类）。
        if isinstance(error, StructuredOutputParseError):
            error_code = ErrorCode.OUTPUT_VALIDATION_FAILED
        # 异常时尽量从已累积的 usage 算 cost（修 error path cost 归 0）：stream 消费中途
        # 失败时 context_wrapper.usage 已累积已完成部分（SDK 无清零）。run_result 不可用
        # （调用前失败，如 build_agent 抛）→ cost 回落 0。
        cost, cost_currency = 0.0, "USD"
        if run_result is not None:
            # error path 防御：usage 结构异常（SDK 异常形态 / 非数值字段）→ cost 回落 0，
            # 不让 _handle_error 二次崩溃（它已是 error path 最后防线）。
            try:
                from .openai_result_mapper import _usage_from
                from .pricing import compute_cost
                cost_amount = compute_cost(model, _usage_from(run_result))
                cost, cost_currency = cost_amount.cost, cost_amount.currency
            except (TypeError, AttributeError, ValueError):
                pass
        return ClaudeRunResult(
            text="",
            success=False,
            duration=duration,
            turns=0,
            cost=cost,
            cost_currency=cost_currency,
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
            # string here is superseded by _handle_error's ErrorCode enum override;
            # retryable=True is what this branch contributes.
            return ("OutputValidationError", True)
        error_msg = str(error).lower()
        error_type = type(error).__name__.lower()
        # response_format unavailable: param-level permanent 400 (third-party endpoint
        # rejects json_schema) -> NOT retryable (retries just re-400; caused 8/8 spins
        # on *-vuln agents, 2026-07-24). Must precede the "unavailable" -> ServiceUnavailable
        # branch so "This response_format type is unavailable now" isn't misclassified.
        if "response_format" in error_msg:
            return ("BadRequestError", False)
        # 速率限制 → 可重试
        if "rate" in error_msg or "limit" in error_msg or error_type == "ratelimiterror":
            return ("RateLimitError", True)
        # 超时 → 不可重试:openai 引擎整体超时=CALL_TIMEOUT(40min wall-clock),失控/stall agent
        # 再跑照样超时,重试只放大成 40min×N 卡死。transient 单请求超时已由 client max_retries
        # 在 HTTP 层兜底(3 次),到这层的 timeout 都是整体级。claude 引擎超时走共享
        # classify_error_for_temporal(仍 retryable),两引擎路径独立、互不影响。
        if "timeout" in error_msg or error_type in ("timeouterror", "timeoutexception", "connecttimeout"):
            return ("TimeoutError", False)
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
    """MaxTurnsExceeded 时伪造一个含 final_output + usage 的对象供 map_run_result 使用。

    usage 来自真实 result.context_wrapper.usage（SDK 在超轮数时已累积，无清零）；
    None 时回落 0（无 result 可用）。final_output 用 stream_collector 累积文本
    （超轮时 SDK result.final_output 不完整）。
    """

    def __init__(self, text: str, usage: Any = None):
        self.final_output = text
        if usage is None:
            class _U:
                input_tokens = 0
                output_tokens = 0
            usage = _U()

        class _CW:
            pass
        self.context_wrapper = _CW()
        self.context_wrapper.usage = usage


class _ReparsedRunResult:
    """L1 轻量重输成功后的最小 RunResult stub。

    仅含 map_run_result 需要的 final_output（= recovered dict）+ context_wrapper.usage
    （带 L1 chat completion 的真实 token，避免统计失真；cost 经 map_run_result
    的 pricing 换算，非 GLM 0.0 早退——见 pricing.py）。
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
