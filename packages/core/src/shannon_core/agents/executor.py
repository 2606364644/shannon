import time
from pathlib import Path
from typing import TYPE_CHECKING

from shannon_core.config.parser import distribute_config, parse_config
from shannon_core.models.agents import AgentName, AGENTS
from shannon_core.models.config import Config
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.models.metrics import AgentMetrics
from shannon_core.utils.atomic_write import atomic_write_json
from shannon_core.utils.billing import is_spending_cap_behavior

from shannon_core.agents.runner import run_claude_prompt
from shannon_core.agents.validators import get_queue_filename, validate_deliverable
from shannon_core.collectors import make_collector
from shannon_core.git_manager import GitManager
from shannon_core.prompts.manager import PromptManager
from shannon_core.renderers import render_deliverable
from shannon_core.services.validate_authentication import auth_state_path

if TYPE_CHECKING:
    from shannon_core.logging.activity_logger import ActivityLogger
    from shannon_core.agents.tool_audit_logger import ToolAuditLogger


def resolve_template_name(
    agent_name: AgentName,
    prompt_override: str | None,
    default_template: str,
    web_url: str,
) -> str:
    """决定 agent 实际使用的 prompt template 名。

    - 显式 prompt_override 优先(不被覆盖)。
    - recon agent 在无 live web target(离线/纯静态)时回退到 recon-static,
      对齐原始 shannon runner.ts:189 的 promptOverride 思路。
    - 其余情况用 AGENTS 字典里的默认 prompt_template。
    """
    if prompt_override:
        return prompt_override
    if agent_name == AgentName.RECON and not web_url:
        return "recon-static"
    return default_template


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
        )
        duration_ms = int((time.monotonic() - start_time) * 1000)

        if result.success and is_spending_cap_behavior(result.turns, result.cost, result.text):
            await GitManager.rollback(deliverables, "spending cap detected")
            raise PentestError(
                f"Spending cap likely reached (turns={result.turns}, cost=${result.cost})",
                "billing",
                retryable=True,
                error_code=ErrorCode.SPENDING_CAP_REACHED,
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
            md = render_deliverable(agent_name, collector.get_all())
            if md is not None:
                (deliverables / defn.deliverable_filename).write_text(md, encoding="utf-8")

        if not skip_artifact_postprocess:
            try:
                await validate_deliverable(deliverables, agent_name)
            except PentestError as e:
                # 诊断不改行为(systematic-debugging 2026-07-17):agent success 但
                # deliverable 缺失时,补充 agent 实际产出信息再 re-raise。当前适用范围=
                # 仍走 self-Write 路径的 agent(recon/vuln/exploit):GLM 长任务 + 子代理
                # 委派后失忆,end_turn 但没执行 Write。pre-recon 在 Plan 1 切到 host 渲染
                # (collector→renderer→必渲染 md),不再走 self-Write,故不会进此分支。
                # 不改 error_code/retryable(仍 OUTPUT_VALIDATION_FAILED,retry cap=3 不变)。
                if e.error_code == ErrorCode.OUTPUT_VALIDATION_FAILED:
                    raise _enrich_missing_deliverable_error(
                        e, deliverables, defn, result) from e
                raise

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


def _enrich_missing_deliverable_error(
    error: PentestError, deliverables: Path, defn, result,
) -> PentestError:
    """诊断不改行为:agent success 但 deliverable 缺失时,把 agent 实际产出信息
    注入 error 后返回新 PentestError(调用方 raise ... from 原 error)。

    根因(systematic-debugging 2026-07-17 定位):GLM 长任务 + 子代理委派后失忆,
    agent end_turn(success=True)但没执行 Write 步骤(最初观测于 pre-recon Turn 147
    「仍在等待子代理」后正常结束却没写 md)。Plan 1 起 pre-recon 切到 host 渲染
    (collector→renderer→必渲染 md),不再走 self-Write,故此分支当前只对仍 self-Write
    的 agent(recon/vuln/exploit)可达。validate_deliverable 只检查文件存在性;这里
    补全诊断,经 session.log_error → workflow.log [ERROR] 行 + activity_failures.log
    可见,便于区分「完全没写」(listing 空)vs「写错文件」(listing 有别的)vs「final
    text 是等待语」(text_len>0 但无实质)。

    不改 error_code / retryable / category → classify_error_for_temporal 与 retry
    cap 行为完全不变(仍 OUTPUT_VALIDATION_FAILED → retryable=True → cap=3)。
    所有 .md 产物 agent 共性(recon/vuln-analysis/exploit-evidence/report);
    vuln 的 exploitation_queue 缺失(同 OUTPUT_VALIDATION_FAILED)也经此 enrich,
    has_structured_output 字段对它尤其有用。
    """
    try:
        listing = sorted(p.name for p in deliverables.iterdir()) if deliverables.exists() else []
    except OSError:
        listing = []
    text = (getattr(result, "text", "") or "").strip()
    turns = getattr(result, "turns", None)
    stop_reason = getattr(result, "stop_reason", None)
    has_structured = getattr(result, "structured_output", None) is not None
    diagnostics = {
        "expected_deliverable": defn.deliverable_filename,
        "deliverables_listing": listing,
        "final_text_len": len(text),
        "final_text_preview": text[:300],
        "final_turns": turns,
        "stop_reason": stop_reason,
        "has_structured_output": has_structured,
    }
    summary = (
        f"diagnostics: dir_has={listing}, text_len={len(text)}, turns={turns}, "
        f"stop_reason={stop_reason}, has_structured_output={has_structured}"
    )
    return PentestError(
        f"{error.message} | {summary}",
        error.category,
        retryable=error.retryable,
        error_code=error.error_code,
        context={**error.context, **diagnostics},
    )
