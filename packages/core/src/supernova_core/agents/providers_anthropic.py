"""
Anthropic Provider 实现

使用 Claude Agent SDK 进行 AI 调用，支持:
- anthropic_api: Anthropic 官方 API
- bedrock: AWS Bedrock
- vertex: Google Cloud Vertex AI
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import (
    ClaudeAgentOptions, PermissionResultAllow, PermissionResultDeny, ResultMessage, query,
)

from supernova_core.models.errors import classify_error_for_temporal

from .llm_json import repair_truncated_json
from .narration import narration_directive
from .openai_output_schema import _extract_json_payload
from .pricing import compute_cost
from .providers import BaseProvider
from .runner import ClaudeRunResult, ProviderConfig, TokenUsage, ToolPolicy
from .tool_audit_logger import ToolAuditLogger

if TYPE_CHECKING:
    from supernova_core.agents.progress_tool import ProgressSpec
    from supernova_core.collectors.base import CollectorBase

logger = logging.getLogger(__name__)


# L2 诊断：non-end_turn stop_reason 的语义提示（按值分支，替换原笼统的
# "may indicate budget/refusal" 说法）。stop_sequence 等 CLI/协议层透传值
# 通常是良性的；只有 refusal / 未知值才倾向真问题。
_STOP_REASON_HINTS = {
    "stop_sequence": "CLI/protocol-level sentinel (typically harmless; not a budget cap or refusal)",
    "refusal": "likely a content refusal",
    "max_tokens": "output hit the token limit",
    "max_duration": "hit the duration cap (not a spending cap)",
    "max_turns": "hit the turn limit",
}
_STOP_REASON_HINT_DEFAULT = "unexpected stop_reason; may indicate budget/refusal or an upstream issue"


def _on_claude_stderr(line: str) -> None:
    """转发 Claude Code 子进程的真实 stderr，避免 SDK 吞掉错误。

    claude_agent_sdk 仅在注册了回调时才会捕获 CLI 的 stderr；而它的
    ProcessError 会把 stderr 字段替换成占位字符串
    (``"Check stderr output for details"``)，从而掩盖真正的失败原因
    (例如 exit 143 / SIGTERM 的真实触发点)。逐行打日志以恢复可见性。
    """
    logger.warning("[claude-cli stderr] %s", line.rstrip())


def make_readonly_permission_guard(
    allowed_roots: list[str | Path] | None, cwd: str,
):
    """Fail-close Claude read/search tools to the selected repository roots."""
    roots = [Path(root).resolve() for root in (allowed_roots or [])]

    async def guard(tool_name: str, tool_input: dict[str, Any], _context: Any):
        normalized = tool_name.lower()
        if normalized not in {"read", "glob", "grep"}:
            return PermissionResultDeny(behavior="deny", message="readonly topology policy")
        raw_path = (
            tool_input.get("file_path") or tool_input.get("path")
            or tool_input.get("directory") or tool_input.get("target")
        )
        if not isinstance(raw_path, str) or not raw_path.strip():
            return PermissionResultDeny(
                behavior="deny", message="specify a path inside a selected repository")
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = Path(cwd) / candidate
        candidate = candidate.resolve(strict=False)
        if not any(candidate.is_relative_to(root) for root in roots):
            return PermissionResultDeny(
                behavior="deny", message="path is outside selected repository roots")
        return PermissionResultAllow(behavior="allow")

    return guard


class AnthropicProvider(BaseProvider):
    """使用 Claude Agent SDK 的 Provider。

    A1（2026-06-27 双引擎解耦修复）：继承 BaseProvider，使 ABC 的 @abstractmethod
    约束与 isinstance 契约对两引擎同时生效（此前为鸭子类型，isinstance 为 False）。
    """

    def __init__(self, config: ProviderConfig):
        super().__init__(config)

    def _get_model(self, model_tier: str) -> str:
        """根据 tier 获取模型名称(委托模块级 resolve_tier_model)。

        优先级: tier-specific override > global model > DEFAULT_MODELS
        """
        from .providers import resolve_tier_model
        return resolve_tier_model(self.config, model_tier)

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
        proxy_url: str | None = None,   # Task 4：per-scan 代理穿线 → CLI 子进程 env
        usage_sink: "UsageSink | None" = None,   # cancel 兜底记账（2026-08-28；CLI 子进程被杀拿不到中途值，占位不写）
        tool_policy: ToolPolicy = "default",
        allowed_roots: list[str | Path] | None = None,
    ) -> ClaudeRunResult:
        """
        调用 Claude Agent SDK 执行 prompt

        Args:
            prompt: 用户提示
            cwd: 工作目录
            model_tier: 模型层级
            output_format: 结构化输出格式 (JSON Schema)
            deliverables_subdir: 产物子目录
            collector: 可选的结构化工具收集器；非空时经 bridge 构造 in-process
                MCP server（set_* 工具）+ allowed_tools 注入 ClaudeAgentOptions。
            progress: 可选的进度工具规格（log_milestone）；非空时追加一个 in-process
                MCP server，驱动认证验证进度步骤条。

        Returns:
            ClaudeRunResult: 执行结果
        """
        start_time = time.time()
        model = self._get_model(model_tier)

        try:
            # collector + progress → 本引擎原生工具（in-process MCP servers + allowed_tools 白名单）。
            # engine-agnostic：caller 只传 CollectorBase/ProgressSpec，provider 经 compose 组装。
            from supernova_core.agents.progress_tool import compose_claude_mcp
            mcp_servers, allowed_tools = compose_claude_mcp(collector, progress)

            # 构建 SDK 配置
            options = self._build_options(
                cwd, model, output_format,
                max_turns_override=max_turns,
                mcp_servers=mcp_servers or None,
                allowed_tools=allowed_tools or None,
                proxy_url=proxy_url,
                tool_policy=tool_policy,
                allowed_roots=allowed_roots,
            )

            # 执行调用
            result_message = await self._execute_query(prompt, options, audit_logger=audit_logger)

            # 计算耗时
            duration = int((time.time() - start_time) * 1000)

            # 提取结果（使用 dispatcher 的 turn_count）
            turn_count = getattr(result_message, "turn_count", 1)
            result = self._extract_result(
                result_message, duration, model, turn_count,
                output_format=output_format,
            )

            # L2: read result-level metadata mounted by _execute_query (L1)
            subtype = getattr(result_message, "result_subtype", None)
            is_error = getattr(result_message, "result_is_error", False)
            api_error_status = getattr(result_message, "api_error_status", None)
            result_errors = getattr(result_message, "result_errors", None)

            # L2 diagnostics: surface early-stops and permission denials
            if result.stop_reason and result.stop_reason != "end_turn":
                logger.warning(
                    "Agent stopped early (stop_reason=%s); %s",
                    result.stop_reason,
                    _STOP_REASON_HINTS.get(result.stop_reason, _STOP_REASON_HINT_DEFAULT),
                )
            permission_denials = getattr(result_message, "permission_denials", None)
            if permission_denials:
                logger.info(
                    "Tool permission denials recorded: %d denial(s)",
                    len(permission_denials),
                )

            # L2: result-failure layer — structured failure signals (highest reliability),
            # evaluated before the spending-cap heuristics.
            if not result.success:
                # 可观测性（2026-08-28 NodeGoat-20260828-054537 injection-exploit 失败调查缺口）：
                # 原始失败信号必须落盘——error_code/retryable 只是分类结果，subtype/is_error/
                # api_error_status/errors 才是根因证据（区分 API 401/403 vs turn 限额 vs 执行错误）。
                # 此前只透传分类不留痕：CLI 安静终止（无 stderr、无崩溃打印）时根因无从定位，
                # 且 result.error 传输链丢失使 PentestError 落 fallback 消息（"execution failed"）。
                # 若本日志有 subtype 而 PentestError message 仍为 fallback → error 在下游丢失的实证。
                logger.warning(
                    "SDK result failure signals: subtype=%s is_error=%s api_error_status=%s "
                    "errors=%s stop_reason=%s turns=%s",
                    subtype, is_error, api_error_status, result_errors,
                    result.stop_reason, result.turns,
                )
                error_code, retryable = self._classify_result_failure(
                    subtype, is_error, api_error_status, result_errors
                )
                result.error_code = error_code
                result.retryable = retryable
                result.error = result.error or (
                    f"SDK result failure: subtype={subtype}, api_error_status={api_error_status}"
                )
                return result

            # Layer 1: message-level spending cap detection
            dispatcher_cap_detected = getattr(result_message, "_dispatcher_spending_cap", False)
            if dispatcher_cap_detected:
                result.success = False
                result.retryable = True
                result.error = result.error or "spending cap (message-level detection)"
                return result

            # Layer 2: behavioral spending cap detection
            # Skip when a non-end_turn stop_reason explains the early termination
            # (e.g., max_duration) — not a spending cap.
            if not result.stop_reason and self._detect_spending_cap_behavior(result, turn_count):
                result.success = False
                result.retryable = True
                result.error = result.error or "spending cap (behavioral detection)"
                return result

            return result

        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            return self._handle_error(e, duration, model)

    def _build_sdk_env(self, proxy_url: str | None = None) -> dict[str, str]:
        """Build SDK subprocess environment variables (aligned with TS claude-executor.ts).

        Task 4: ``proxy_url`` 非空时注入 HTTPS_PROXY/HTTP_PROXY + NO_PROXY(loopback)，
        把 CLI 子进程 outbound 流量经 per-scan 代理（host_profile）。
        ``proxy_url=None`` 不写入代理键（backward-compat 铁律）。
        """
        sdk_env: dict[str, str] = {}

        # Base config —— P3c 阶段 0：self.config.max_output_tokens 优先（None 回落 env）
        if self.config.max_output_tokens is not None:
            sdk_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(self.config.max_output_tokens)
        else:
            max_tokens = os.getenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000")
            if max_tokens:
                sdk_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = max_tokens

        # Provider-specific config
        if self.type == "anthropic_api":
            if self.config.api_key:
                sdk_env["ANTHROPIC_API_KEY"] = self.config.api_key
            # auth_token / base_url 显式注入（glm-anthropic 走 token、无 api_key）：
            # 不能只靠下方 PASSTHROUGH 从进程 env 兜——worker env 无 ANTHROPIC_* 时
            # CLI 子进程 0 凭据 → "Not logged in"（2026-07-30 web 引擎错配根因之一）。
            if self.config.auth_token:
                sdk_env["ANTHROPIC_AUTH_TOKEN"] = self.config.auth_token
            if self.config.base_url:
                sdk_env["ANTHROPIC_BASE_URL"] = self.config.base_url
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

        # IS_SANDBOX=1 绕过 claude CLI 的 root 守卫(permission_mode=bypassPermissions + getuid()==0
        # → exit(1) "--dangerously-skip-permissions cannot be used with root/sudo"). 仅 root 时设:
        # worker 容器(root)跑 LLM agent 必需; host supernova-user(非根 getuid()!=0)不触发该守卫故不设
        # (保持 host 路径零改动, 两路互不干扰). 语义"本进程已在沙箱内", bypassPermissions 本就是扫描
        # worker 的意图(C1 worker 为 root 设计: 无 USER/--global safe.directory/repos root 友好).
        # 2026-07-14 bug#4.
        if hasattr(os, "getuid") and os.getuid() == 0:
            sdk_env["IS_SANDBOX"] = "1"

        # Task 4: per-scan 出口代理（host_profile）。NO_PROXY 仅保 loopback —— 未 mapped
        # 的 host（LLM/temporal）经 HostResolverPlugin.resolve_dns 返 (None,None) → proxy.py
        # 回落自身默认 DNS 仍可达，故无需枚举内部 host（见 Task 4 NO_PROXY resolution）。
        if proxy_url:
            sdk_env["HTTPS_PROXY"] = proxy_url
            sdk_env["HTTP_PROXY"] = proxy_url
            sdk_env["NO_PROXY"] = "127.0.0.1,localhost"

        return sdk_env

    def _resolve_max_turns(self, max_turns_override: int | None) -> int:
        """解析 max_turns，优先级：vuln 外部 override > P3c config > CLAUDE_MAX_TURNS env > 10000。

        默认 10000 对齐原始 TS shannon（claude-executor.ts: maxTurns:10_000）：max_turns 仅作
        runaway 硬兜底、不限制正常长任务；成本兜底靠 spending cap（utils/billing，同 TS）。
        提取为纯函数便于阶段 0 测试（原内联在 _build_options:276）。
        """
        if max_turns_override is not None:
            return max_turns_override
        if self.config.max_turns is not None:
            return self.config.max_turns
        return int(os.getenv("CLAUDE_MAX_TURNS", "10000"))

    def _build_options(
        self,
        cwd: str,
        model: str,
        output_format: dict | None = None,
        max_turns_override: int | None = None,
        mcp_servers: dict | None = None,
        allowed_tools: list[str] | None = None,
        proxy_url: str | None = None,
        tool_policy: ToolPolicy = "default",
        allowed_roots: list[str | Path] | None = None,
    ) -> ClaudeAgentOptions:
        """构建 ClaudeAgentOptions"""
        options = ClaudeAgentOptions(
            model=model,
            cwd=cwd,
            permission_mode="bypassPermissions",  # 无交互环境必需
        )
        if tool_policy == "readonly-code":
            if not allowed_roots:
                raise ValueError("readonly-code policy requires allowed_roots")
            options.permission_mode = "default"
            options.can_use_tool = make_readonly_permission_guard(allowed_roots, cwd)
        if tool_policy == "readonly-code":
            if mcp_servers or allowed_tools:
                raise ValueError("readonly-code policy cannot inject collector/progress tools")
            # tools is the availability set (unlike allowed_tools, which only bypasses prompts).
            options.tools = ["Read", "Glob", "Grep"]
            options.strict_mcp_config = True
            options.mcp_servers = {}

        # max_turns: high "runaway" ceiling. 优先级见 _resolve_max_turns。
        max_turns = self._resolve_max_turns(max_turns_override)
        options.max_turns = max_turns

        # 添加结构化输出:包装成 claude_agent_sdk 信封契约 {type:'json_schema', schema:{...}}。
        # subprocess_cli.py:400 仅当 output_format['type']=='json_schema' 才提取 schema 加
        # --json-schema(协议级结构化输出 + AJV 校验 + SDK error_max_structured_output_retries
        # 重试)。裸 schema(type='object')会被忽略 → CLI 退化为 best-effort 文本提取 →
        # 结构化输出概率性漏盘(史例:NodeGoat injection exploitation queue 3 连跪;vuln
        # agent Phase 2 起走 collector 主通道,不经此路)。现行裸 schema 活跃用户:executor
        # 定向重查 _RECHECK_OUTPUT_SCHEMA、authz gitnexus judge(whitebox activities 的
        # inline schema)、chain_verdict 的 CHAIN_VERDICT_SCHEMA——这里统一包装。对齐
        # TS queue-schemas.ts:106 + SDK types.py:1894。
        if output_format:
            options.output_format = {"type": "json_schema", "schema": output_format}

        # 添加 adaptive thinking
        if self._is_adaptive_thinking_enabled():
            from claude_agent_sdk.types import ThinkingConfigAdaptive
            options.thinking = ThinkingConfigAdaptive(type="adaptive")

        # Environment variables via _build_sdk_env
        options.env = self._build_sdk_env(proxy_url=proxy_url)

        # 捕获 Claude CLI 子进程的真实 stderr。否则 SDK 会丢弃 stderr，
        # 用占位字符串掩盖失败原因。
        options.stderr = _on_claude_stderr

        # Part B: append narration-language directive as system prompt. SDK maps
        # preset/append → `--append-system-prompt` (verified in claude_agent_sdk
        # subprocess_cli.py:235-238) — true system-prompt position append, does
        # NOT replace the base system prompt. None when disabled → unchanged.
        directive = narration_directive()
        if directive:
            options.system_prompt = {
                "type": "preset",
                "preset": "claude_code",
                "append": directive,
            }

        # in-process MCP servers（collector 的 set_* 工具 + progress 的 log_milestone，
        # 经 compose_claude_mcp 组装成 dict）+ allowed_tools 白名单。allowed_tools 让 SDK
        # MCP server 注册的工具被发现/可调用（不传则这些工具对 agent 不可见）。在
        # bypassPermissions 模式下,内置工具(Bash/Read/Grep 等)不受 allowed_tools 限制、
        # 仍可用 —— Task 6 真机探针(scripts/validate_glm_mcp_tool_probe.py)实测 GLM 同时
        # 调 Bash/Read 与 7 个 set_* 工具,旧注释「不传 allowed_tools CLI 默认全放行会漏调
        # set_*」的因果模型是错的。
        if mcp_servers:
            options.mcp_servers = dict(mcp_servers)
        if allowed_tools:
            options.allowed_tools = list(allowed_tools)

        return options

    def _is_adaptive_thinking_enabled(self) -> bool:
        """检查是否启用 adaptive thinking。

        P3c 阶段 0：self.config.adaptive_thinking 优先（None 回落 CLAUDE_ADAPTIVE_THINKING env）。
        """
        if self.config.adaptive_thinking is not None:
            return self.config.adaptive_thinking
        return os.getenv("CLAUDE_ADAPTIVE_THINKING", "true").lower() != "false"

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
        audit_logger: ToolAuditLogger | None = None,
    ) -> ResultMessage:
        """执行 query 调用并返回最终结果。

        SDK 流跑在独立 task，本协程只 await 它——裸 task.cancel()（temporal workflow
        cancel / start_to_close_timeout / worker shutdown 均是裸 cancel）打在普通
        await 点必进 except；而直接 async for 时 cancel 会打进 SDK 内部 anyio shield
        传不进去（task 卡死 + CLI 子进程残留，/tmp/cancel_probe.py 假 CLI 实证；SDK
        close() 自述 raw asyncio cancellation 会跳过 SIGTERM escalation）。
        """
        from .message_dispatcher import MessageDispatcher

        dispatcher = dispatcher or MessageDispatcher(audit_logger=audit_logger)
        inner = asyncio.ensure_future(
            self._consume_query_stream(prompt, options, dispatcher))
        pre = self._snapshot_active_children()
        try:
            # 必经 shield 桥：直接 await inner 时，本 task 挂在 inner(fut_waiter)上，
            # Task.cancel() 会去 cancel fut_waiter=inner 并返回——cancel 被 SDK 内部
            # anyio shield 吸干后本 task 永收不到 CancelledError（永挂，/tmp/mini_probe3
            # 实证）。shield 的裸 future 让 cancel 必达本协程且不误伤 inner。
            return await asyncio.shield(inner)
        except asyncio.CancelledError:
            # 先杀本调用新起的子进程（同步动作必达）：子进程死 → stdio EOF → 即使
            # cancel 传不进的 inner 也会解卡自然回收；inner.cancel 善后（能退则退，
            # 不 await 强收——except 块内 await wait_for 组合在 py3.13 实测卡死），
            # 最后原样 re-raise 保持取消语义。
            self._terminate_new_children(pre)
            inner.cancel()
            raise

    async def _consume_query_stream(
        self,
        prompt: str,
        options: ClaudeAgentOptions,
        dispatcher: "MessageDispatcher",
    ) -> ResultMessage:
        """消费 query 事件流（在 _execute_query 的独立 task 里跑）。"""
        final_result: ResultMessage | None = None

        # aclosing（spec 2026-08-28-temporal-native-cancel-design 修 C）：cancel 打断
        # async for 或 complete break 提前退出时显式关闭 SDK query 生成器（GeneratorExit
        # 进生成器 → SDK 清理 CLI 子进程）；不吞 CancelledError。旧代码靠 GC，不确定。
        async with contextlib.aclosing(query(prompt=prompt, options=options)) as events:
            async for event in events:
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
        # L1: mount result-level metadata so _extract_result / call() can read it
        final_result.result_is_error = dispatcher.result_is_error
        final_result.result_subtype = dispatcher.result_subtype
        final_result.stop_reason = dispatcher.stop_reason
        final_result.permission_denials = dispatcher.permission_denials
        final_result.api_error_status = dispatcher.api_error_status
        final_result.result_errors = dispatcher.result_errors
        return final_result

    @staticmethod
    def _snapshot_active_children() -> frozenset:
        """快照 SDK 活跃 CLI 子进程集（cancel 清理的 diff 基线）。

        lazy import + 全吞异常：SDK 私有符号（_ACTIVE_CHILDREN）结构变化时降级
        no-op（杀不了比炸掉 cancel 路径好）。并发他路子进程在快照内 → 不误杀。
        """
        try:
            from claude_agent_sdk._internal.transport.subprocess_cli import (
                _ACTIVE_CHILDREN,
            )
            return frozenset(_ACTIVE_CHILDREN)
        except Exception:  # noqa: BLE001 - SDK 结构变化降级 no-op
            return frozenset()

    @staticmethod
    def _terminate_new_children(pre: frozenset) -> None:
        """SIGTERM 本调用新起的 CLI 子进程（_ACTIVE_CHILDREN - pre），best-effort。"""
        try:
            import signal

            from claude_agent_sdk._internal.transport.subprocess_cli import (
                _ACTIVE_CHILDREN,
            )
        except Exception:  # noqa: BLE001 - SDK 结构变化降级 no-op
            return
        for p in list(_ACTIVE_CHILDREN):
            if p in pre:
                continue
            try:
                p.send_signal(signal.SIGTERM)  # 对齐 SDK atexit reaper 的信号语义
            except Exception:  # noqa: BLE001 - 进程已死/权限等不拦取消
                pass

    def _extract_result(
        self,
        result_message: ResultMessage,
        duration: int,
        model: str,
        turn_count: int = 1,
        output_format: dict | None = None,
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

        # 提取成本（自算：tokens × 价目表，spec §4.5）
        cost_amount = self._extract_cost(result_message, model)

        # 提取结构化输出
        structured_output = None
        if hasattr(result_message, "structured_output") and result_message.structured_output:
            structured_output = result_message.structured_output
        elif output_format and text:
            # 兜底：SDK 未解析出 structured_output 时（GLM 后端常见，final 文本夹
            # 中文说明+JSON），从 collected_text 提取 JSON。对齐 openai 引擎
            # （openai_result_mapper + openai_output_schema._extract_json_payload）。
            payload = _extract_json_payload(text)
            if payload:
                try:
                    structured_output = json.loads(payload)
                except (json.JSONDecodeError, ValueError):
                    # 截断修复兜底（spec 2026-08-19 §3.1）：网关流中断在最终
                    # 消息尾部时，从半截 JSON 救回 N-1 条完整元素，避免
                    # structured_output=None → 静默漏盘 → 整轨报废三轮重跑。
                    repaired = repair_truncated_json(payload)
                    if repaired is not None:
                        structured_output = json.loads(repaired)
                        items = (structured_output.get("vulnerabilities")
                                 if isinstance(structured_output, dict)
                                 else structured_output)
                        logger.warning(
                            "structured_output recovered via truncation repair "
                            "(anthropic engine): payload_len=%d repaired_len=%d "
                            "recovered_items=%s",
                            len(payload), len(repaired),
                            len(items) if isinstance(items, (list, dict)) else "?",
                        )
                    else:
                        structured_output = None
                else:
                    logger.info(
                        "structured_output recovered from collected_text fallback "
                        "(SDK result_message.structured_output was empty)"
                    )

        # L2: derive success from result-level failure semantics + persist stop_reason.
        # Reads the metadata mounted by _execute_query (L1).
        is_error = getattr(result_message, "result_is_error", False)
        subtype = getattr(result_message, "result_subtype", None)
        stop_reason = getattr(result_message, "stop_reason", None)
        success = not (is_error or (subtype is not None and subtype.startswith("error_")))

        return ClaudeRunResult(
            text=text,
            success=success,
            duration=duration,
            turns=turn_count,
            cost=cost_amount.cost,
            cost_currency=cost_amount.currency,
            model=model,
            structured_output=structured_output,
            stop_reason=stop_reason,
            tokens=tokens,
        )

    def _extract_tokens(self, result_message: ResultMessage) -> TokenUsage:
        """从 ResultMessage 提取 token 统计。

        claude-agent-sdk 的 ``ResultMessage.usage`` 是 ``dict[str, Any]``（SDK
        types.py:1156），不是带属性的对象——必须用 ``dict.get()`` 访问；
        ``getattr(dict, key)`` 会静默返回默认值（曾致 claude 引擎全 profile
        cost=0，含智谱：CLI 给非零 usage dict 也被读成 0）。保留对象形态兼容。
        """
        usage = getattr(result_message, "usage", None)
        if usage is None:
            usage = getattr(result_message, "model_usage", None)

        if usage is None:
            return TokenUsage()

        def _get(key: str) -> int:
            if isinstance(usage, dict):
                return usage.get(key, 0) or 0
            return getattr(usage, key, 0) or 0

        return TokenUsage(
            input_tokens=_get("input_tokens"),
            output_tokens=_get("output_tokens"),
            cache_creation_input_tokens=_get("cache_creation_input_tokens"),
            cache_read_input_tokens=_get("cache_read_input_tokens"),
        )

    def _extract_cost(self, result_message: ResultMessage, model: str):
        """自算成本（spec §4.5）：tokens × 价目表，不再读 SDK total_cost_usd。"""
        tokens = self._extract_tokens(result_message)
        return compute_cost(model, tokens, pricing_override=self.config.pricing_override)

    def _classify_result_failure(
        self,
        subtype: str | None,
        is_error: bool,
        api_error_status: int | None,
        errors: list[str] | None,
    ) -> tuple[str, bool]:
        """Map structured ResultMessage failure signals to (error_code, retryable).

        Structured signals (subtype, then api_error_status) win over string sniffing.
        Only when is_error is set with no recognised structured signal do we fall back
        to ``classify_error_for_temporal`` on the collected error text.
        """
        # 1) Explicit SDK failure subtypes (highest priority)
        if subtype == "error_max_turns":
            return ("ExecutionLimitError", False)
        if subtype == "error_during_execution":
            return ("TransientError", True)
        if subtype == "error_max_structured_output_retries":
            return ("OutputValidationError", True)

        # 2) HTTP status of the failing call (only meaningful when is_error)
        if is_error and api_error_status is not None:
            if api_error_status == 429:
                return ("RateLimitError", True)
            if api_error_status in (500, 502, 503, 529):
                return ("TransientError", True)
            if api_error_status == 402:
                return ("BillingError", True)
            if api_error_status == 401:
                return ("AuthenticationError", False)
            if api_error_status == 403:
                return ("PermissionError", False)

        # 3) Fallback: classify the collected error text
        error_text = "; ".join(errors) if errors else "SDK result error"
        return classify_error_for_temporal(Exception(error_text))

    def _handle_error(
        self,
        error: Exception,
        duration: int,
        model: str,
    ) -> ClaudeRunResult:
        """处理错误 — 使用 classify_error_for_temporal 进行集中式分类"""
        # 异常路径可观测性（2026-08-28 NodeGoat-20260828-054537 后续）：type/repr 必须
        # 落盘——此前该分支无任何日志，而 375ba4c2 的可观测性只覆盖 ResultMessage 路径
        # （L2 result-failure），异常路径是黑洞：CLI 层静默失败时根因无从定位。
        logger.warning(
            "provider exception path: type=%s error=%r duration_ms=%s",
            type(error).__name__, error, duration,
        )
        # 空消息异常兜底：str(e) 为空（如无参 TimeoutError()）时保类型名——
        # 否则 error=""（falsy）→ executor 侧 `result.error or fallback` 落 fallback
        # 消息，分类信息（error_code）与原始形态全部丢失（现场实证形态）。
        error_msg = str(error) or repr(error)

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
