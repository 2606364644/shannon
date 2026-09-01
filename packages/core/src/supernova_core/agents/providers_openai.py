"""OpenAI Provider（基于 openai-agents，Chat Completions 模式接第三方 OpenAI 兼容接口）。

设计见 docs/superpowers/specs/2026-06-17-openai-agents-engine-design.md。
与 AnthropicProvider 双引擎并存，经 SUPERNOVA_AI_PROVIDER=openai_compatible 切换。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from pathlib import Path
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
from .runner import ClaudeRunResult, TokenUsage, ToolPolicy

logger = logging.getLogger(__name__)
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
    """原地清洗 messages：把 assistant tool_call 的非法 arguments 修好或兜底 ``"{}"``。

    第三方 openai 兼容端点（火山方舟 ARK 等）消费侧校验 ``tool_call.function.arguments``
    必须是合法 JSON；GLM 偶发吐残缺/markdown 串，openai-agents 的 Chat Completions 模式
    无状态全量重发，会把它原样塞回 history 再次发送 → 端点 400 ``Invalid request body``。
    本函数在请求发出前把非法串修好（复用 ``repair_json_arguments``），修不好兜底 ``"{}"``
    （探针确认 ARK 接受 ``{}``）止血 400。合法 arguments 与无 tool_calls 的消息原样不动。

    契约不止「合法 JSON 串」，而是「合法 JSON **object**」：端点 parse arguments 后调
    ``.items()`` 要求 dict，根为 list/str/number/bool 的合法 JSON（如 ``"[]"``）同样 400
    （回归 __legacy__ NodeGoat-20260820-162849 pre-recon，'list' object has no
    attribute 'items'）。与防线1（bridge 09770734 的「合法 JSON 非对象」拒收）跨层同契约。

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
                # 空串/非串（同族畸形形态）一并归一，不留网关 parse 失败入口。
                fn["arguments"] = "{}"
                continue
            repaired = repair_json_arguments(args)
            if repaired is None:
                fn["arguments"] = "{}"
                continue
            # repair 契约保证返回值可 json.loads；根非 object 同样兜 "{}"。
            fn["arguments"] = repaired if isinstance(json.loads(repaired), dict) else "{}"
    return messages


def _strip_tools_strict(tools: Any) -> Any:
    """原地剥掉 tools 里每个 function 的 ``strict`` 字段。

    openai-agents 的 ``function_tool`` 默认 ``strict_mode=True``，序列化后工具
    ``function`` 带 ``"strict": true``。这是 OpenAI 原生 API 的结构化输出参数；
    第三方 openai 兼容网关（litellm 等）会把它当作启用 grammar-constrained
    decoding 的信号。若上游部署用了 speculative decoding（如 DFLASH），
    grammar 约束在流中途崩（``MidStreamFallbackError: ... does not support
    grammar-constrained decoding yet``），被 openai SDK 吞成泛化的
    ``"An error occurred during streaming"``。strict 是 OpenAI 专有参数，第三方
    端点普遍不认/会错配；发包前剥掉，与 ``_sanitize_messages`` 同一道防线。

    回归：__legacy__ probe-d6168171（2026-08-18，llm-proxy.futuoa.com +
    deepseek-v4-flash-coder）。决定性对照：同 key/模型/工具，``strict: true``
    必挂，``strict: false`` / 不带均通。
    """
    if not isinstance(tools, list):
        return tools
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if isinstance(fn, dict):
            fn.pop("strict", None)
    return tools


def _wrap_client_for_argument_sanitize(
    client: AsyncOpenAI, disable_thinking: bool = False
) -> Any:
    """返回 client 的代理：透传所有属性，仅 ``chat.completions.create`` 发包前清洗。

    发包前两道防线：归一化 messages 的非法 tool_call arguments（止血 400）+
    剥离 tools.strict（止血 litellm/DFLASH grammar 约束流中途崩）。

    ``disable_thinking=True`` 时第三道注入：请求体加 ``thinking={"type":"disabled"}``。
    推理快照模型（deepseek-v4-flash-0731 等）默认开 thinking 且 reasoning token 计入
    completion_tokens——chain verdict 单链 mean 133s 撑爆 15min 窗口（2026-09-01
    NodeGoat-20260901-015018）；实测 llm-proxy 唯一有效开关即该参数（官方
    chat_template_kwargs 风格被网关忽略），单轮 19s→7s。做在 client 包装层 =
    该 provider 所有请求（多轮 agent / 单次调用 / subagent，共用同一 client）
    统一生效。对齐 anthropic 引擎 ProviderConfig.adaptive_thinking 语义：
    False=显式禁用，True/None=模型默认（见 OpenAIProvider._get_client）。

    注意必须经 ``extra_body`` 注入而非顶层 kwargs：openai SDK 的 create() 是显式
    签名（不接受任意 kwarg），顶层传 ``thinking`` 在客户端本地即 TypeError
    ``AsyncCompletions.create() got an unexpected keyword argument 'thinking'``
    （2026-09-01 NodeGoat-20260901-060640 真机回归：gn-discovery 31ms 全灭）；
    ``extra_body`` 是 SDK 官方逃生舱，合并进 request JSON body 顶层——与直连
    curl 顶层参数等效。
    """
    original_create = client.chat.completions.create

    async def _sanitized_create(*args: Any, **kwargs: Any) -> Any:
        messages = kwargs.get("messages")
        if messages is not None:
            kwargs["messages"] = _sanitize_messages(messages)
        tools = kwargs.get("tools")
        if tools is not None:
            kwargs["tools"] = _strip_tools_strict(tools)
        if disable_thinking:
            extra_body = kwargs.get("extra_body") or {}
            extra_body.setdefault("thinking", {"type": "disabled"})
            kwargs["extra_body"] = extra_body
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
            # max_retries 默认 1（对齐注释与 test_get_client_defaults_when_env_unset 锁定的
            # 意图；曾漂移为 3）：非流式请求超时是确定性的（同样请求重发照样超），重试只放大
            # 白烧（回归 NodeGoat-20260818-133852 xss-vuln：stall 4 次×300s≈20min 拖死主
            # agent）。流式请求的 transient stall 由 activity 层重试兜底，不缺这一层。
            kwargs["timeout"] = float(os.getenv("SUPERNOVA_OPENAI_HTTP_TIMEOUT", "300"))
            kwargs["max_retries"] = int(os.getenv("SUPERNOVA_OPENAI_MAX_RETRIES", "1"))
            self._client = _wrap_client_for_argument_sanitize(
                AsyncOpenAI(**kwargs),
                # ProviderConfig.adaptive_thinking=False → 全请求注入 thinking 禁用
                # （工作区 config.yaml 一键关整个扫描的 thinking；语义对齐
                # providers_anthropic._is_adaptive_thinking_enabled：仅 False 显式禁用）。
                disable_thinking=self.config.adaptive_thinking is False,
            )
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

    def build_agent(
        self, model: str, output_format: dict | None, extra_tools: list | None = None,
        tool_policy: ToolPolicy = "default",
        allowed_roots: list[str | Path] | None = None,
    ) -> Agent:
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
        if tool_policy == "readonly-code":
            if not allowed_roots:
                raise ValueError("readonly-code policy requires allowed_roots")
            if extra_tools:
                raise ValueError("readonly-code policy cannot add extra tools")
            from .tools_openai import build_readonly_code_tools
            tools = build_readonly_code_tools()
        else:
            tools = build_tools() + (extra_tools or [])
        return Agent(
            name="shannon-openai-agent",
            instructions=self._instructions(),  # None when disabled
            tools=tools,
            model=chat_model,
            model_settings=ModelSettings(include_usage=True),
            output_type=None,
        )

    def _subagent_call_timeout(self) -> float:
        """子代理 run 消费的 wall-clock 超时（秒），兜底永久 stall。

        子代理体量（只读代码阅读）小于主 agent，默认 900s < 主 2400s
        （SUPERNOVA_OPENAI_CALL_TIMEOUT）；stall 时先于主超时抛错，父 agent 收
        [task error] 后可自行兜底，不拖满主 call_timeout 连坐整次执行
        （回归 NodeGoat-20260818-133852：marked 子代理 stall 28min 拖死 xss-vuln）。
        P3c 阶段 0：self.config 优先（None 回落 env）。
        """
        if self.config.subagent_call_timeout is not None:
            return self.config.subagent_call_timeout
        return float(os.getenv("SUPERNOVA_OPENAI_SUBAGENT_CALL_TIMEOUT", "900"))

    def _make_subagent_runner(self, model: str, cwd: str, proxy_url: str | None = None):
        """构建子代理 runner：代码阅读 Agent（read/glob/grep）跑 prompt，返回 final_output。

        spec 改动 4a：对齐 Claude Code CLI 的 Task tool —— 父 agent 调用 `task`
        function_tool 时，spawn 一个只读代码的子代理（read_file/glob/grep，无 bash/write/task），
        跑完返回其 final_output，保持父上下文精简。

        子代理 ToolContext 不注入 subagent_run（防嵌套递归）。
        Task 4: ``proxy_url`` 透传给子代理 ToolContext（与主 agent 同一 per-scan 代理；
        子代理工具集仅 read/glob/grep 当前不读 proxy_url，但保持对称注入供未来扩展）。

        流式 + wall-clock 兜底（回归 2026-08-18 xss-vuln）：非流式 Runner.run 生成完成前
        零字节返回，HTTP 读超时（默认 300s）等于"整个生成必须 300s 内完成"，长分析必超时
        且 SDK 原样重发照样超时（确定性死局，4 次×300s 白烧 28min）。对齐主 agent 的
        run_streamed——流式 chunk 持续重置读超时，长生成扛得住；再包 wait_for 兜底
        stream 永久 stall（此前子代理零超时，只能靠主 2400s 拖死连坐）。
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
            start = time.monotonic()
            logger.info("task subagent start: %.80s", prompt.replace("\n", " "))
            result = Runner.run_streamed(
                subagent,
                input=prompt,
                context=ToolContext(cwd=cwd, proxy_url=proxy_url),  # 子代理同 cwd，无 subagent_run（防递归）
                max_turns=max_turns,
            )

            async def _consume() -> None:
                async for _event in result.stream_events():
                    pass  # 消费即驱动；事件形态不作断言（子代理黑盒问题由日志兜底）

            await asyncio.wait_for(_consume(), timeout=self._subagent_call_timeout())
            duration = int((time.monotonic() - start) * 1000)
            logger.info("task subagent finish: duration_ms=%d", duration)
            return str(result.final_output)

        return run

    def _reparse_timeout(self) -> float:
        """L1 reparse 的 wall-clock 超时（秒）。

        reparse 在 call() 的 wait_for(call_timeout) **之外**执行，此前无任何
        wall-clock 约束：非流式 create + client 级 max_retries 放大时（stall
        4 次×300s≈20min）可把 agent 拖在"已出结果后的兜底步骤"上。reparse 语义
        是轻量快速兜底（失败即弃、进 L2），独立短超时封顶。env 可配。
        """
        return float(os.getenv("SUPERNOVA_OPENAI_REPARSE_TIMEOUT", "120"))

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
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    timeout=self._reparse_timeout(),  # per-request 覆盖 client 300s，单次尽快失败
                ),
                timeout=self._reparse_timeout(),      # wall-clock 兜底（含 SDK 内部重试）
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
        usage_sink: "UsageSink | None" = None,   # cancel 兜底记账（2026-08-28）
        tool_policy: ToolPolicy = "default",
        allowed_roots: list[str | Path] | None = None,
    ) -> ClaudeRunResult:
        start_time = time.time()
        model = self._get_model(model_tier)
        streaming = None  # SDK RunResultStreaming，供 error path（_handle_error/MaxTurnsExceeded）提累积 usage
        try:
            # collector + progress → 本引擎原生工具（FunctionTool list）。
            # engine-agnostic：caller 只传 CollectorBase/ProgressSpec，provider 经 compose 组装。
            from supernova_core.agents.progress_tool import compose_openai_extra_tools
            extra_tools = compose_openai_extra_tools(collector, progress) or None
            agent = self.build_agent(model, output_format, extra_tools=extra_tools, tool_policy=tool_policy, allowed_roots=allowed_roots)
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
                        subagent_run=None if tool_policy == "readonly-code" else self._make_subagent_runner(model, cwd, proxy_url),
                        allowed_roots=tuple(Path(root).resolve() for root in allowed_roots),
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
                run_result = result
            except MaxTurnsExceeded:
                # result（run_streamed 返回）的 context_wrapper.usage 已累积到超轮数的 token
                # （SDK 无清零），用它算 cost（弃旧 _MaxTurnsStub 硬编码 0 usage）。
                mt_usage = streaming.context_wrapper.usage if streaming is not None else None
                run_result = _MaxTurnsStub(stream_collector.text, mt_usage)
                stop_reason = "max_turns"
            finally:
                # 取消/超时路径也收尾（spec 2026-08-28-temporal-native-cancel-design 修 D）：
                # close→_flush_turn 非幂等，此处唯一调用点（正常/MaxTurns/cancel/timeout 共用），
                # suppress 只拦 Exception——CancelledError(BaseException) 照穿不吞。
                with contextlib.suppress(Exception):
                    await stream_collector.close()

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
        except asyncio.CancelledError:
            # cancel 兜底记账（2026-08-28 authcheck 超时丢账）：Temporal
            # start_to_close_timeout cancel 掉 activity 时 CancelledError 穿透
            # except Exception（BaseException），正常返回路径的 usage 拿不到。
            # context_wrapper.usage 已累积已完成 turn 的消耗（SDK 无清零）——
            # 归一 + 计价（与 _handle_error error path 同款）写 usage_sink 后
            # 原样上抛，cancel/重试语义不变。归一+计价失败不拦 cancel。
            if usage_sink is not None and streaming is not None:
                try:
                    from .openai_result_mapper import _usage_from
                    from .pricing import compute_cost
                    u = _usage_from(streaming)
                    ca = compute_cost(model, u, pricing_override=self.config.pricing_override)
                    usage_sink.record(
                        model=model,
                        input_tokens=u.input_tokens,
                        output_tokens=u.output_tokens,
                        cache_read_tokens=u.cache_read_input_tokens,
                        cache_creation_tokens=u.cache_creation_input_tokens,
                        cost_usd=ca.cost, cost_currency=ca.currency)
                except (TypeError, AttributeError, ValueError):
                    pass
            raise
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
        # error 文案：openai SDK 把流内错误包成泛化 APIError（message="An error
        # occurred during streaming"），真实根因藏在 body 属性（如 litellm
        # MidStreamFallbackError "DFLASH speculative decoding does not support
        # grammar-constrained decoding yet"）。str(error) 只给泛化 message，日志
        # 无细节、排查需手挖 APIError.body。body 非空时附进文案，可观测化。
        # 回归：__legacy__ probe-d6168171（2026-08-18）。
        error_body = getattr(error, "body", None)
        if error_body:
            error_msg = f"{error} | body={error_body}"
        else:
            error_msg = str(error)
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
                cost_amount = compute_cost(model, _usage_from(run_result), pricing_override=self.config.pricing_override)
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
            error=error_msg,
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
