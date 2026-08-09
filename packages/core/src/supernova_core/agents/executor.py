import time
from pathlib import Path
from typing import TYPE_CHECKING

from supernova_core.config.parser import distribute_config, parse_config
from supernova_core.models.agents import AgentName, AGENTS
from supernova_core.models.config import Config
from supernova_core.models.errors import ErrorCode, PentestError
from supernova_core.models.metrics import AgentMetrics
from supernova_core.utils.atomic_write import atomic_write_json
from supernova_core.utils.billing import is_spending_cap_behavior

from supernova_core.agents.runner import run_claude_prompt
from supernova_core.agents.validators import get_queue_filename, validate_deliverable
from supernova_core.collectors import make_collector
from supernova_core.git_manager import GitManager
from supernova_core.prompts.manager import PromptManager
from supernova_core.renderers import render_deliverable
from supernova_core.services.validate_authentication import auth_state_path

if TYPE_CHECKING:
    from supernova_core.logging.activity_logger import ActivityLogger
    from supernova_core.agents.tool_audit_logger import ToolAuditLogger


def resolve_template_name(
    agent_name: AgentName,
    prompt_override: str | None,
    default_template: str,
    web_url: str,
) -> str:
    """决定 agent 实际使用的 prompt template 名。

    - 显式 prompt_override 优先(不被覆盖)。
    - 其余情况用 AGENTS 字典里的默认 prompt_template。
    - spec 2026-08-03 白盒去动态:RECON 的 prompt_template 已固定为 recon-static
      (纯静态,只要仓库就开扫),不再按 web_url 分叉动态/静态。web_url 参数保留为
      兼容签名(逻辑层不再使用);动态 live 侦察职责移交黑盒端点验证 agent。
    """
    if prompt_override:
        return prompt_override
    return default_template


def _result_cost_context(result) -> dict:
    """失败路径 raise PentestError 时携带 result 的 cost/tokens，供 activities 失败
    分支记进 metrics（修 error path cost 归 0——失败 agent 也记已产生的真实 LLM 消耗）。

    成功路径走 AgentMetrics（见 execute 末尾）；失败路径 raise PentestError 原本丢弃了
    result.cost，现经 PentestError.context 桥接到 activities 的 except PentestError →
    end_agent → metrics。tokens 可能为 None（provider 异常路径未提），各 token 字段回落 None。
    """
    tokens = result.tokens
    return {
        "cost_usd": result.cost,
        "cost_currency": result.cost_currency,
        "model": result.model,
        "num_turns": result.turns,
        "input_tokens": tokens.input_tokens if tokens else None,
        "output_tokens": tokens.output_tokens if tokens else None,
        "cache_read_tokens": tokens.cache_read_input_tokens if tokens else None,
        "cache_creation_tokens": tokens.cache_creation_input_tokens if tokens else None,
    }


class AgentExecutor:
    def __init__(self, prompt_manager: PromptManager):
        self.prompt_manager = prompt_manager

    async def execute(
        self,
        agent_name: AgentName,
        repo_path: str,
        web_url: str = "",
        deliverables_path: str | None = None,
        config_path: str | None = None,
        api_key: str | None = None,
        pipeline_testing: bool = False,
        prompt_variables: dict[str, str] | None = None,
        prompt_override: str | None = None,
        structured_output_schema: dict | None = None,
        audit_logger: "ActivityLogger | None" = None,
        tool_audit_logger: "ToolAuditLogger | None" = None,
        max_turns: int | None = None,
        skip_artifact_postprocess: bool = False,
        provider_config: dict | None = None,   # P3c 阶段 1：穿线下传 run_claude_prompt
        queue_root: str | None = None,   # spec 2026-08-08：读 queue 的根（黑盒=白盒 repo_path/deliverables），透传到 render_deliverable
    ) -> AgentMetrics:
        defn = AGENTS[agent_name]
        repo = Path(repo_path)
        if not deliverables_path:
            raise ValueError(
                "deliverables_path is required (deliverables 落 session, 不再 fallback 到 repo)"
            )
        deliverables = Path(deliverables_path)
        deliverables.mkdir(parents=True, exist_ok=True)
        await GitManager.ensure_repository(deliverables)

        config: Config | None = None
        if config_path:
            config = parse_config(config_path)
        distributed = distribute_config(config)

        variables = {
            "web_url": web_url,
            "repo_path": str(repo),
            "deliverables_path": str(deliverables),
            "scratchpad_path": str(deliverables.parent / "scratchpad"),
            # 统一注入 auth-state 路径（对齐 TS agent-execution.ts:133）。
            # workspace_path = deliverables.parent（≡ input.workspace_path，
            # 见 spec §3.3）。仅"有 auth 配置 + prompt include shared-session
            # partial"的 agent 生效；其余 manager strip block，no-op（spec §4）。
            "AUTH_STATE_FILE": str(auth_state_path(deliverables.parent)),
        }
        if config:
            variables["browser_engine"] = config.browser_engine
        if prompt_variables:
            variables.update(prompt_variables)
        template_name = resolve_template_name(
            agent_name, prompt_override, defn.prompt_template, web_url,
        )
        prompt = self.prompt_manager.load_sync(
            template_name,
            variables=variables,
            config=distributed,
            pipeline_testing=pipeline_testing,
        )

        await GitManager.create_checkpoint(deliverables, agent_name)

        # host-rendered deliverables:pre-recon(Plan 1)+ 5 vuln agent(Plan 3)用声明式
        # collector 接 set_* → host renderer 确定性渲染 md(对齐 TS)。其余 agent
        # make_collector 返 None(无 collector 通道,走 self-Write,不改行为)。
        collector = make_collector(agent_name)

        start_time = time.monotonic()
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=str(repo),
            model_tier=defn.model_tier,
            api_key=api_key,
            deliverables_subdir=str(deliverables.relative_to(repo)) if deliverables.is_relative_to(repo) else None,
            structured_output_schema=structured_output_schema,
            audit_logger=audit_logger,
            tool_audit_logger=tool_audit_logger,
            max_turns=max_turns,
            collector=collector,
            provider_config=provider_config,   # P3c 阶段 1
        )
        duration_ms = int((time.monotonic() - start_time) * 1000)

        if result.success and is_spending_cap_behavior(result.turns, result.cost, result.text):
            await GitManager.rollback(deliverables, "spending cap detected")
            raise PentestError(
                f"Spending cap likely reached (turns={result.turns}, cost=${result.cost})",
                "billing",
                retryable=True,
                error_code=ErrorCode.SPENDING_CAP_REACHED,
                context=_result_cost_context(result),
            )

        if not result.success:
            await GitManager.rollback(deliverables, "execution failure")
            # 透传 provider 设的合法 ErrorCode（如 OUTPUT_VALIDATION_FAILED）；
            # provider 的字符串 error_code（Temporal error type，非 enum）不透传，
            # 保持 AGENT_EXECUTION_FAILED 现有行为（避免破坏 RateLimit/Timeout 分类）。
            error_code = (
                result.error_code
                if isinstance(result.error_code, ErrorCode)
                else ErrorCode.AGENT_EXECUTION_FAILED
            )
            raise PentestError(
                result.error or f"Agent {agent_name.value} execution failed",
                "validation",
                retryable=result.retryable,
                error_code=error_code,
                context=_result_cost_context(result),
            )

        queue_filename = get_queue_filename(agent_name)
        if (
            not skip_artifact_postprocess
            and result.structured_output is not None
            and queue_filename
        ):
            queue_path = deliverables / queue_filename
            atomic_write_json(queue_path, result.structured_output)

        # host 渲染写 md:有 collector 通道的 agent(Plan 1 = pre-recon)在 queue 写盘
        # 之后、validate 之前,用 collector payload 确定性渲染 deliverable md。这样
        # validate_deliverable 见文件即过(无需把 pre-recon validator 改 no-op)。对齐
        # TS agent-execution.ts:295-297 writeDeliverable。render_deliverable 对无
        # collector 的 agent 返 None → 跳过写盘(self-Write 路径不动)。
        if not skip_artifact_postprocess and collector is not None:
            md = render_deliverable(agent_name, collector.get_all(), deliverables, queue_root=queue_root)
            if md is not None:
                (deliverables / defn.deliverable_filename).write_text(md, encoding="utf-8")

        if not skip_artifact_postprocess:
            await validate_deliverable(deliverables, agent_name)

        await GitManager.commit(deliverables, agent_name)

        return AgentMetrics(
            duration_ms=duration_ms,
            cost_usd=result.cost,
            cost_currency=result.cost_currency,
            num_turns=result.turns,
            model=result.model,
            structured_output=result.structured_output,
            stop_reason=result.stop_reason,
            input_tokens=result.tokens.input_tokens if result.tokens else None,
            output_tokens=result.tokens.output_tokens if result.tokens else None,
            cache_read_tokens=result.tokens.cache_read_input_tokens if result.tokens else None,
            cache_creation_tokens=result.tokens.cache_creation_input_tokens if result.tokens else None,
        )

