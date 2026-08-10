"""``log_milestone`` 里程碑工具——agent 在登录各阶段调一次 → 发 StepEvent(complete)
→ 前端步骤条推进。

与 collectors/bridge.py 同构的双引擎自定义工具（agent 主动调、host 侧产生副作用），
但语义不同：collector 收集 deliverable payload，progress tool 发可观测 StepEvent。

- openai：``FunctionTool``（on_invoke_tool 闭包）
- claude：``SdkMcpTool``（in-process MCP server，无子进程/IPC）

核心逻辑是纯函数 :func:`emit_milestone`（接收 session），builder 包一层
:func:`get_audit_session` 取当前 run 的 session——这样纯逻辑可独立测、无需全局态。
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressStep:
    """一个进度步骤：key（事件层 step name）+ intent（步骤语义描述）。"""

    key: str
    intent: str


@dataclass(frozen=True)
class ProgressSpec:
    """一个 agent 的进度工具规格：phase + 该 phase 声明的步骤序列。

    phase 与 StepEvent.phase / PhaseEvent.phase 三者一致，step.key 与
    PhaseEvent.steps / StepEvent.name 一致——reducer 按 name 匹配推进步骤条。
    """

    phase: str
    steps: tuple[ProgressStep, ...]

    @property
    def step_keys(self) -> tuple[str, ...]:
        return tuple(s.key for s in self.steps)

    @property
    def step_by_key(self) -> dict[str, ProgressStep]:
        return {s.key: s for s in self.steps}

    @property
    def json_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "step": {
                    "type": "string",
                    "enum": list(self.step_keys),
                    "description": "刚完成的登录阶段；每阶段调一次。",
                }
            },
            "required": ["step"],
        }


# auth-validation（认证档案页「测试登录」）的 4 步进度。
# SSOT：blackbox AuthValidationWorkflow 声明 PhaseEvent、本模块 handler 发 StepEvent 都用它的 key。
AUTH_VALIDATION_PROGRESS = ProgressSpec(
    phase="auth-validation",
    steps=(
        ProgressStep("navigate", "导航到登录页"),
        ProgressStep("fill_credentials", "填写凭据"),
        ProgressStep("submit", "提交登录表单"),
        ProgressStep("verify_session", "校验已登录会话"),
    ),
)


async def emit_milestone(step_key: str, spec: ProgressSpec, session) -> str:
    """发一个 StepEvent(complete) 标记 ``step_key`` 完成。

    builder 从 :func:`get_audit_session` 取当前 session 后调本函数。无效 step 不发
    事件、返错误串（让模型重发），与 collector bridge 的错误处理同构。
    """
    step = spec.step_by_key.get(step_key)
    if step is None:
        return (f"log_milestone: unknown step '{step_key}'. "
                f"Valid: {', '.join(spec.step_keys)}")
    await session.log_step(step.key, spec.phase, "complete", intent=step.intent)
    return f"log_milestone: {step.key} recorded"


# ---------------------------------------------------------------------------
# 双引擎 builder —— 与 collectors/bridge.py 同构（一份 spec 生成两引擎工具）
# ---------------------------------------------------------------------------


def build_openai_progress_tool(spec: ProgressSpec):
    """``log_milestone`` → openai-agents FunctionTool（on_invoke_tool 闭包）。

    handler 经 :func:`get_audit_session` 取当前 run 的 session 后调
    :func:`emit_milestone`。非法 JSON arguments 返错误串让模型重发（不静默兜底），
    与 collector bridge 一致。
    """
    from agents import FunctionTool

    from supernova_core.agents.llm_json import repair_json_arguments
    from supernova_core.audit.session_registry import get_audit_session

    async def _on_invoke(ctx, input_json: str) -> str:
        repaired = repair_json_arguments(input_json)
        if repaired is None:
            return ("log_milestone: ERROR — arguments is not valid JSON. "
                    "Resend log_milestone with valid JSON matching the schema.")
        step_key = (json.loads(repaired) or {}).get("step", "")
        return await emit_milestone(step_key, spec, get_audit_session())

    return FunctionTool(
        name="log_milestone",
        description=("报告一个登录阶段完成以推进进度步骤条。"
                     "在导航到登录页 / 填完凭据 / 提交登录 / 校验会话后各调一次。"),
        params_json_schema=spec.json_schema,
        on_invoke_tool=_on_invoke,
        strict_json_schema=False,
    )


def _make_progress_sdk_tool(spec: ProgressSpec):
    """``log_milestone`` → claude-agent-sdk SdkMcpTool（in-process，handler 闭包）。"""
    from claude_agent_sdk import SdkMcpTool

    from supernova_core.audit.session_registry import get_audit_session

    async def _handler(args: dict) -> dict:
        step_key = (args or {}).get("step", "")
        msg = await emit_milestone(step_key, spec, get_audit_session())
        envelope: dict = {"content": [{"type": "text", "text": msg}]}
        if msg.startswith("log_milestone: unknown step"):
            envelope["is_error"] = True
        return envelope

    return SdkMcpTool(
        name="log_milestone",
        description=("报告一个登录阶段完成以推进进度步骤条。"
                     "在导航到登录页 / 填完凭据 / 提交登录 / 校验会话后各调一次。"),
        input_schema=spec.json_schema,
        handler=_handler,
    )


def build_claude_progress_server(
    spec: ProgressSpec, server_name: str = "supernova-progress"
):
    """打包 log_milestone 成 in-process MCP server（无子进程/IPC），挂 options.mcp_servers。"""
    from claude_agent_sdk import create_sdk_mcp_server

    return create_sdk_mcp_server(
        name=server_name, tools=[_make_progress_sdk_tool(spec)]
    )


# ---------------------------------------------------------------------------
# 工厂 + 组装 —— provider.call 委托这两个纯 helper（engine-agnostic）
# ---------------------------------------------------------------------------


def make_progress(agent_name) -> ProgressSpec | None:
    """按 agent 分发 ProgressSpec（镜像 ``collectors.make_collector``）。

    validate-authentication agent 获得 auth-validation 4 步进度；其余返 None
    （无 progress 工具通道，行为不变）。
    """
    from supernova_core.models.agents import AgentName

    if agent_name == AgentName.VALIDATE_AUTH:
        return AUTH_VALIDATION_PROGRESS
    return None


def compose_openai_extra_tools(collector, progress) -> list:
    """合并 collector 工具 + progress 工具为 openai extra_tools list。

    provider.call 委托本函数，避免在引擎调用路径里散落组装逻辑（可独立测）。
    """
    tools: list = []
    if collector is not None:
        from supernova_core.collectors.bridge import build_openai_tools

        tools.extend(build_openai_tools(collector))
    if progress is not None:
        tools.append(build_openai_progress_tool(progress))
    return tools


def compose_claude_mcp(collector, progress) -> "tuple[dict, list[str]]":
    """合并 collector server + progress server 为 claude mcp_servers dict + allowed_tools。

    返回 (servers, allowed_tools)。provider.call 委托本函数。
    """
    servers: dict = {}
    allowed: list[str] = []
    if collector is not None:
        from supernova_core.collectors.bridge import build_claude_mcp_server

        servers["shannon-collector"] = build_claude_mcp_server(collector)
        allowed.extend(collector.tool_names())
    if progress is not None:
        servers["supernova-progress"] = build_claude_progress_server(progress)
        allowed.append("log_milestone")
    return servers, allowed


