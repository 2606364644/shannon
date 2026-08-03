import time
from pathlib import Path
from urllib.parse import urlparse

from temporalio import activity
from temporalio.exceptions import ApplicationError as ApplicationFailure

from supernova_core.models.agents import AgentName
from supernova_core.models.errors import ErrorCode, PentestError, classify_error_for_temporal
from supernova_core.models.retry import agent_retry_category, retry_for
from supernova_core.utils.security import validate_target_url, check_url_reachable
from supernova_core.utils.credential_validator import validate_credentials
from supernova_core.agents.executor import AgentExecutor
from supernova_core.prompts.manager import PromptManager
from supernova_core.utils.paths import (
    BLACKBOX_SUBDIR,
    WHITEBOX_SUBDIR,
    blackbox_dir,
    resolve_deliverables_path,
)

from .shared import BlackboxActivityInput
from supernova_blackbox.services.exploitation_checker import QueueValidationResult


def _get_deliverables_path(input: BlackboxActivityInput) -> Path:
    # C1 Phase B（黑盒 web 化）：workspace_path（web=event_file.parent=scan_dir）由
    # BlackboxScanWorkflow 算好传入（workflows.py C1 Phase B）。优先用它作 deliverables 根，
    # 使产物落 scan_dir，与 web DeliverablesReader 读取口径对齐——对齐 whitebox _get_paths
    # （activities.py:42-61，2026-07-30 修过同类分裂：旧 resolve_deliverables_path(workspace_name=
    # scan_id) 落 workspaces/<scan_id>/ 平铺目录，web 在 scan_dir/deliverables 读不到 → 0 漏洞）。
    # 无 workspace_path（activity 被直接调用、不经 workflow）回落 resolve_deliverables_path。
    if input.workspace_path:
        return Path(input.workspace_path) / input.deliverables_subdir
    return resolve_deliverables_path(
        repo_path=input.repo_path,
        deliverables_subdir=input.deliverables_subdir,
        workspace_name=input.workspace_name,
    )


# C1 Phase B（黑盒 web 化）：这两个 activity 仅 worker 容器路径调用（BlackboxScanWorkflow 接入）；
# CLI run_scan 不调用（CLI 自行 inline AuditSession/heartbeat，零改动）。setup_display 注入进程
# 全局 AuditSession 使后续 activity 的 get_audit_session() 可用；finalize_summary 写 scan_end 事件
# + 清理全局 session。直采 core audit 体系（whitebox 的 supernova_whitebox.audit.* 是 core shim）。


@activity.defn
async def setup_display(input: BlackboxActivityInput) -> None:
    """C1 前导 activity（黑盒 web 化）：构造 headless core AuditSession + 注入 event_file。

    worker 容器无 TTY，用 use_rich=False + Console()（自动检测非 TTY → 纯文本）。
    AuditSession.initialize(workflow_id, event_file=input.event_file) → WorkflowLogger 挂
    StructuredEventRenderer 写 events.ndjson（web live 页可见）。参照 whitebox setup_display
   （activities.py:1760-1807），但全用 core import（whitebox audit.* 仅 core 的 compat shim）。
    """
    from rich.console import Console
    from supernova_core.logging import configure_logging
    from supernova_core.logging.log_bus import LogBus
    from supernova_core.models.metrics import SessionMetadata
    from supernova_core.audit.session import AuditSession
    from supernova_core.audit.session_registry import set_audit_session
    from supernova_core.runtime.heartbeat import start_heartbeat

    if input.workspace_path:
        ws_path = Path(input.workspace_path)
    else:
        ws_path = (
            Path(input.repo_path).parent / "workspaces"
            / (input.workspace_name or "scan"))
    meta = SessionMetadata(
        id=input.workspace_name or ws_path.name,
        web_url=input.web_url,
        repo_path=input.repo_path,
        output_path=str(ws_path.parent),
    )
    # per-scan diagnostic.log（worker 容器入口挂 LogBusHandler；幂等，对齐 whitebox setup_display）。
    configure_logging(log_dir=ws_path / "logs")
    console = Console()  # auto-detects non-TTY in pipes -> plain text per event
    session = AuditSession(meta, use_rich=False, console=console)
    await session.initialize(workflow_id=meta.id, event_file=input.event_file)
    # attach 把散落 getLogger 诊断汇入 dispatcher（起 drain task），否则裸 logger 走 lastResort stderr。
    await LogBus.attach(session.dispatcher)
    set_audit_session(session)
    # heartbeat 由 daemon 线程持续写 <ws>/heartbeat，finalize_summary 停止（终态自停兜底）。
    await start_heartbeat(ws_path)


@activity.defn
async def finalize_summary(input: BlackboxActivityInput, summary: dict) -> None:
    """C1 后置 activity（黑盒 web 化）：log_workflow_complete（→ StructuredEventRenderer 写 scan_end
    事件）+ 清 AuditSession/heartbeat。summary 由 workflow 从 self._state 构建（等价 whitebox
    finalize_summary activities.py:1810-1844）。CLI 路径不调用。
    """
    from supernova_core.logging.log_bus import LogBus
    from supernova_core.models.audit import WorkflowSummary
    from supernova_core.audit.session_registry import (
        NullAuditSession, clear_audit_session, get_audit_session,
    )
    from supernova_core.runtime.heartbeat import stop_heartbeat

    session = get_audit_session()
    if not isinstance(session, NullAuditSession):
        # cost/cost_currency 取 session.get_metrics()（MetricsTracker 累积所有 agent，完整），
        # 非 summary dict（其 total_cost 来自 workflow state.agent_metrics，可能残缺）。对齐 whitebox。
        final_metrics = await session.get_metrics() or {}
        ws = WorkflowSummary(
            status=summary.get("status", "failed"),
            total_duration_ms=summary.get("total_duration_ms", 0),
            total_cost_usd=final_metrics.get("total_cost_usd") or 0.0,
            cost_currency=final_metrics.get("cost_currency") or "USD",
            completed_agents=summary.get("completed_agents", []),
            agent_metrics=summary.get("agent_metrics", {}),
            error=summary.get("error"),
        )
        # final flush（dispatch 余下 LogEvent）在 log_workflow_complete 关闭 workflow_logger 之前。
        await LogBus.drain_and_detach()
        await session.log_workflow_complete(ws)
    await stop_heartbeat()  # 停 heartbeat daemon（启动于 setup_display）；终态自停兜底
    clear_audit_session()


@activity.defn
async def run_blackbox_preflight(input: BlackboxActivityInput) -> None:
    try:
        # URL safety and reachability checks (mandatory for blackbox)
        if input.web_url:
            pinned_ip = validate_target_url(input.web_url)
            reachable = await check_url_reachable(
                input.web_url,
                pinned_ip=pinned_ip,
                original_host=urlparse(input.web_url).hostname,
            )
            if not reachable:
                raise PentestError(
                    f"Target URL is not reachable: {input.web_url}",
                    category="preflight",
                    error_code=ErrorCode.TARGET_UNREACHABLE,
                )

        # Config parsing validation
        if input.config_path:
            from supernova_core.config.parser import parse_config
            try:
                parse_config(input.config_path)
            except PentestError:
                raise
            except Exception as exc:
                raise PentestError(
                    f"Config parsing failed: {exc}",
                    category="config",
                    error_code=ErrorCode.CONFIG_PARSE_ERROR,
                ) from exc

        # Repo is optional for blackbox — skip git checks entirely
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_blackbox_auth_validation(input: BlackboxActivityInput) -> None:
    from supernova_core.audit.session_registry import get_audit_session
    from supernova_core.audit.session_tool_audit_logger import SessionToolAuditLogger
    from supernova_core.models.audit import AgentEndResult
    from supernova_core.models.agents import AgentName

    agent_name = AgentName.VALIDATE_AUTH
    attempt = activity.info().attempt
    max_attempts = retry_for(agent_retry_category(agent_name.value)).maximum_attempts
    session = get_audit_session()
    tool_audit_logger = SessionToolAuditLogger(session, agent_name.value, attempt)
    agent_start = time.monotonic()
    try:
        from supernova_core.services.validate_authentication import validate_authentication

        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)

        await session.start_agent(agent_name.value, f"agent={agent_name.value}", attempt=attempt)
        await tool_audit_logger.initialize()
        deliverables = _get_deliverables_path(input)
        result = await validate_authentication(
            web_url=input.web_url,
            config_path=input.config_path,
            workspace_path=input.workspace_path or "",
            prompt_manager=prompt_manager,
            executor=executor,
            repo_path=input.repo_path or "",
            deliverables_path=str(deliverables),
            api_key=input.api_key,
            tool_audit_logger=tool_audit_logger,
        )
        await tool_audit_logger.close(
            success=True, duration_ms=int((time.monotonic() - agent_start) * 1000))
        await session.end_agent(agent_name.value, AgentEndResult(
            success=True, duration_ms=int((time.monotonic() - agent_start) * 1000),
            cost_usd=0.0, attempt_number=attempt))
        if not result.success:
            raise PentestError(
                f"Authentication validation failed: {result.failure_detail or 'unknown'}",
                category="preflight",
                retryable=False,
                error_code=ErrorCode.AUTH_LOGIN_FAILED,
            )
    except PentestError as e:
        await tool_audit_logger.close(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000))
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value, attempt=attempt, max_attempts=max_attempts)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        await tool_audit_logger.close(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000))
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value, attempt=attempt, max_attempts=max_attempts)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_exploit_agent(input: BlackboxActivityInput) -> dict:
    from supernova_core.audit.session_registry import get_audit_session
    from supernova_core.audit.session_tool_audit_logger import SessionToolAuditLogger
    from supernova_core.models.audit import AgentEndResult

    vuln_type: str = input.vuln_type
    agent_name = AgentName(f"{vuln_type}-exploit")
    attempt = activity.info().attempt
    max_attempts = retry_for(agent_retry_category(agent_name.value)).maximum_attempts
    session = get_audit_session()
    tool_audit_logger = SessionToolAuditLogger(session, agent_name.value, attempt)
    agent_start = time.monotonic()
    try:
        from supernova_blackbox.agents.exploit_executor import ExploitExecutor

        deliverables = _get_deliverables_path(input)
        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)
        exploit = ExploitExecutor(executor)

        await session.start_agent(agent_name.value, f"agent={agent_name.value}", attempt=attempt)
        await tool_audit_logger.initialize()
        metrics = await exploit.execute(
            agent_name=agent_name,
            vuln_type=vuln_type,
            workspace_path=deliverables.parent,
            deliverables_path=deliverables,
            web_url=input.web_url,
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
            tool_audit_logger=tool_audit_logger,
            correlation_context=input.correlation_context,
        )
        await tool_audit_logger.close(success=True, duration_ms=metrics.duration_ms)
        await session.end_agent(agent_name.value, AgentEndResult(
            success=True,
            duration_ms=metrics.duration_ms,
            cost_usd=metrics.cost_usd or 0.0,
            cost_currency=metrics.cost_currency,
            attempt_number=attempt,
            model=metrics.model,
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
            cache_read_tokens=metrics.cache_read_tokens,
            cache_creation_tokens=metrics.cache_creation_tokens,
        ))
        return metrics.model_dump()
    except PentestError as e:
        await tool_audit_logger.close(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000))
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value, attempt=attempt, max_attempts=max_attempts)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        await tool_audit_logger.close(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000))
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value, attempt=attempt, max_attempts=max_attempts)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_endpoint_verify(input: BlackboxActivityInput) -> dict:
    """spec 2026-08-03: 端点 live 验证 agent。读白盒端点清单 + auth-state(AgentExecutor
    基层注入),对每端点做 live 验证 + 路由转发前缀探测,产 endpoint_verify.json 到 blackbox/。

    功能性失败(agent 崩/超时/LLM 不可用/无产出) → 返回 degraded(endpoint_verify=None),
    workflow 据此降级 exploit 全打(不 raise / 不重试 / 不中断)。endpoint_verify 是增强
    功能,失败=现状行为(零回归),故不浪费 budget 重试。"""
    from supernova_core.audit.session_registry import get_audit_session
    from supernova_core.audit.session_tool_audit_logger import SessionToolAuditLogger
    from supernova_core.models.audit import AgentEndResult
    from supernova_core.models.agents import ALL_VULN_CLASSES

    agent_name = AgentName.ENDPOINT_VERIFY
    attempt = activity.info().attempt
    session = get_audit_session()
    tool_audit_logger = SessionToolAuditLogger(session, agent_name.value, attempt)
    agent_start = time.monotonic()
    try:
        from supernova_blackbox.agents.endpoint_verify_executor import EndpointVerifyExecutor

        deliverables = _get_deliverables_path(input)
        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)
        verifier = EndpointVerifyExecutor(executor)

        await session.start_agent(agent_name.value, f"agent={agent_name.value}", attempt=attempt)
        await tool_audit_logger.initialize()
        result = await verifier.execute(
            deliverables_path=deliverables,
            workspace_path=deliverables.parent,
            web_url=input.web_url,
            vuln_classes=list(ALL_VULN_CLASSES),
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
            tool_audit_logger=tool_audit_logger,
        )
        dur_ms = int((time.monotonic() - agent_start) * 1000)
        await tool_audit_logger.close(success=True, duration_ms=dur_ms)
        await session.end_agent(agent_name.value, AgentEndResult(
            success=True,
            duration_ms=result.get("duration_ms", dur_ms) if isinstance(result, dict) else dur_ms,
            cost_usd=(result.get("cost_usd") or 0.0) if isinstance(result, dict) else 0.0,
            cost_currency=result.get("cost_currency") if isinstance(result, dict) else None,
            attempt_number=attempt,
        ))
        return result
    except Exception as e:
        dur_ms = int((time.monotonic() - agent_start) * 1000)
        await tool_audit_logger.close(success=False, duration_ms=dur_ms)
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=dur_ms, cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        # 降级:不 raise,返回 degraded。exploit 据此全打(零回归)。
        return {"endpoint_verify": None, "reason": f"{type(e).__name__}: {e}"}


@activity.defn
async def assemble_report(input: BlackboxActivityInput) -> None:
    try:
        from supernova_core.services.report_assembler import ReportAssembler
        from supernova_core.models.agents import ALL_VULN_CLASSES
        from supernova_core.services.findings_renderer import FindingsRenderer

        deliverables = _get_deliverables_path(input)
        vuln_classes: list[str] = list(ALL_VULN_CLASSES)
        bb = blackbox_dir(deliverables)
        bb.mkdir(parents=True, exist_ok=True)
        report_path = bb / "comprehensive_security_assessment_report.md"

        report_config = None
        if input.config_path:
            from supernova_core.config.parser import parse_config
            cfg = parse_config(input.config_path)
            report_config = cfg.report
        await FindingsRenderer.render_findings_from_queues(
            deliverables, report_config,
            queue_subdir=WHITEBOX_SUBDIR, findings_subdir=BLACKBOX_SUBDIR)

        # AU-6: exploit queue→evidence 闭环——未覆盖条目写入 evidence 未覆盖节，
        # ReportAssembler 读 evidence 全文时自动带入最终报告。
        from supernova_blackbox.services.coverage_renderer import close_coverage_gaps
        await close_coverage_gaps(deliverables, vuln_classes)

        # Order invariant: close_coverage_gaps mutates evidence above; ReportAssembler
        # reads evidence here — do not reorder (uncovered section would be missed).
        # ReportAssembler 收 bb（blackbox/）：黑盒 evidence/findings 在 blackbox/ 自洽。
        await ReportAssembler.assemble(bb, vuln_classes, report_path)
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def validate_exploitation_queue(input: BlackboxActivityInput) -> QueueValidationResult:
    """在 activity 内执行 exploitation queue 校验（文件 I/O 须在 sandbox 外）。

    ExploitationChecker.validate_queue 内部走 aiofiles（async_path_exists/async_read_file
    → run_in_executor），workflow sandbox 内直调会抛 NotImplementedError。本 activity 包装它，
    文件 I/O 全在 activity 内完成，workflow 侧只 execute_activity 拿回 QueueValidationResult。

    返回类型注解必须保留：temporalio 默认 converter 序列化 dataclass→json/plain，反序列化时
    只有拿到 ret_type 作 type_hint 才能还原 dataclass（worker/_workflow_instance.py:
    ``ret_types = [ret_type] if ret_type else None``）。缺注解→ret_type=None→workflow 侧拿到
    dict→validation.valid 抛 AttributeError（真机 exploitation gating 崩，单测不经 converter
    往返而漏）。见 test_validate_exploitation_queue_roundtrips_as_dataclass。
    """
    from supernova_blackbox.services.exploitation_checker import ExploitationChecker
    try:
        # 根因 1 修复：查白盒 queue 的根 = repo_path/deliverables_subdir（白盒产物所在），
        # 而非黑盒自己 _get_deliverables_path（workspace_path/deliverables_subdir，空）。
        # standalone（无 repo_path）回落 _get_deliverables_path。
        deliverables = (
            Path(input.repo_path) / input.deliverables_subdir
            if input.repo_path else _get_deliverables_path(input)
        )
        return await ExploitationChecker.validate_queue(
            vuln_type=input.vuln_type,
            deliverables_path=deliverables,
        )
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_report_agent(input: BlackboxActivityInput) -> dict:
    from supernova_core.audit.session_registry import get_audit_session
    from supernova_core.audit.session_tool_audit_logger import SessionToolAuditLogger
    from supernova_core.models.audit import AgentEndResult

    agent_name = AgentName.REPORT
    attempt = activity.info().attempt
    max_attempts = retry_for(agent_retry_category(agent_name.value)).maximum_attempts
    session = get_audit_session()
    tool_audit_logger = SessionToolAuditLogger(session, agent_name.value, attempt)
    agent_start = time.monotonic()
    try:
        deliverables = _get_deliverables_path(input)
        # 对齐 assemble_report / finalize_report：黑盒报告落 deliverables/blackbox/。
        # report-executive prompt 假设 assemble 已在 {{DELIVERABLES_PATH}} 下拼好报告等它
        # Edit、validator 也校验同一路径。若传顶层 deliverables，agent 在顶层找不到
        # blackbox/ 里的拼接报告 → 发散自建、写错位置 → Missing deliverable（重试到死）。
        bb = blackbox_dir(deliverables)
        bb.mkdir(parents=True, exist_ok=True)
        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)

        await session.start_agent(agent_name.value, f"agent={agent_name.value}", attempt=attempt)
        await tool_audit_logger.initialize()
        metrics = await executor.execute(
            agent_name=agent_name,
            repo_path=str(bb),
            web_url=input.web_url,
            deliverables_path=str(bb),
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
            tool_audit_logger=tool_audit_logger,
        )
        await tool_audit_logger.close(success=True, duration_ms=metrics.duration_ms)
        await session.end_agent(agent_name.value, AgentEndResult(
            success=True,
            duration_ms=metrics.duration_ms,
            cost_usd=metrics.cost_usd or 0.0,
            cost_currency=metrics.cost_currency,
            attempt_number=attempt,
            model=metrics.model,
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
            cache_read_tokens=metrics.cache_read_tokens,
            cache_creation_tokens=metrics.cache_creation_tokens,
        ))
        return metrics.model_dump()
    except PentestError as e:
        await tool_audit_logger.close(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000))
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value, attempt=attempt, max_attempts=max_attempts)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        await tool_audit_logger.close(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000))
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value, attempt=attempt, max_attempts=max_attempts)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def finalize_report(input: BlackboxActivityInput) -> None:
    try:
        from supernova_core.services.report_assembler import ReportAssembler
        from supernova_core.interfaces.report_output_provider import NoOpReportOutputProvider

        deliverables = _get_deliverables_path(input)
        bb = blackbox_dir(deliverables)
        bb.mkdir(parents=True, exist_ok=True)
        report_path = bb / "comprehensive_security_assessment_report.md"

        session_path = Path(input.workspace_path) / "session.json" if input.workspace_path else None
        if session_path:
            await ReportAssembler.inject_model_info(report_path, session_path)

        provider = NoOpReportOutputProvider()
        await provider.generate(report_path, deliverables)
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def log_phase_start_activity(input: BlackboxActivityInput) -> None:
    from supernova_core.audit.session_registry import get_audit_session
    phase = input.phase or input.workspace_name or "unknown"
    await get_audit_session().log_phase_start(phase)


@activity.defn
async def log_phase_complete_activity(input: BlackboxActivityInput) -> None:
    from supernova_core.audit.session_registry import get_audit_session
    phase = input.phase or input.workspace_name or "unknown"
    await get_audit_session().log_phase_complete(phase)


@activity.defn
async def log_info_activity(input: BlackboxActivityInput) -> None:
    from supernova_core.audit.session_registry import get_audit_session
    try:
        await get_audit_session().log_info(input.info_message, input.info_level)
    except Exception:
        pass  # best-effort: 显示侧通道失败绝不影响扫描（尤其 except 块里调，避免替换原异常）


@activity.defn
async def load_correlation_context(corr_workspace_path: str) -> dict | None:
    """读关联 workspace 的 topology/boundaries 作为 exploitation 上下文（B2）。

    在 activity 内做文件 I/O（workflow sandbox 禁 Path.exists/read_text）。
    文件缺失返回 None（workflow 退回原逻辑）。corr_workspace_path 由 workflow 在
    sandbox 外用 ws_root 拼好后以 str 传入（Path 不便跨 Temporal payload 序列化）。
    """
    import json
    corr_workspace_path = Path(corr_workspace_path)
    dlv = corr_workspace_path / "deliverables"
    topo_f, bound_f = dlv / "cross-service-topology.json", dlv / "trust-boundaries.json"
    if not (topo_f.exists() and bound_f.exists()):
        return None
    return {
        "topology": json.loads(topo_f.read_text(encoding="utf-8")),
        "boundaries": json.loads(bound_f.read_text(encoding="utf-8")),
    }


@activity.defn
async def resolve_blackbox_engine(input: BlackboxActivityInput) -> str:
    """preflight: 解析 config + 解析/校验 engine + 写 deny rules + 写 stealth config。

    把原 workflow run() 体内 config/engine 初始化块的文件 I/O 与副作用（parse_config 读
    yaml、check_available 走 shutil.which、sync_code_path_deny_rules 写 settings.json、
    write_config 写 stealth config）整体挪进 activity——workflow sandbox 禁这些操作。
    返回 engine_name 供后续 exploit 循环复用（workflow 侧不再持有不可序列化的 engine 对象）。
    错误语义与原 workflow 块一致（BROWSER_ENGINE_UNAVAILABLE）。
    """
    try:
        from supernova_core.config.parser import parse_config
        from supernova_core.services.settings_writer import sync_code_path_deny_rules
        from supernova_core.services.browser_engine import BrowserEngineFactory
        import supernova_core.services.engines  # noqa: F401 – registers engines

        cfg = parse_config(input.config_path) if input.config_path else None
        engine_name = cfg.browser_engine if cfg else "agent-browser"
        try:
            engine = BrowserEngineFactory.get_engine(engine_name)
        except KeyError as e:
            raise PentestError(
                f"No browser engine registered as '{engine_name}'.",
                "browser",
                error_code=ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
            ) from e
        if not engine.check_available():
            raise PentestError(
                f"Browser engine '{engine.name}' is not available. "
                f"Install it with: npm install -g {engine.name} && {engine.name} install",
                "browser",
                error_code=ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
            )
        if cfg and cfg.rules and cfg.rules.avoid:
            sync_code_path_deny_rules(cfg.rules.avoid)
        if input.repo_path:
            engine.write_config(input.repo_path)
        return engine_name
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def detect_whitebox_results(
    deliverables_path: str,
    vuln_classes: list[str],
    correlation_deliverables_path: str | None,
) -> dict:
    """preflight: 检测单仓/关联 workspace 的有效白盒 queue（文件 I/O，sandbox 禁）。

    复刻原 workflow run() 的 has_valid_whitebox_results + §6.2 correlation 检测逻辑：
    单仓无结果才查关联（ADD 语义，任一有效即 skip recon）。state 更新与 log 留在
    workflow 侧（用返回值驱动）。corr 路径由 workflow 在 sandbox 外拼好后以 str 传入。
    返回 {has_whitebox_results, found_classes, corr_classes, has_recon_deliverable}。

    对齐 TS validateDeliverablesExist（activities.ts:1330）：recon_deliverable.md 是全局攻击面
    情报（exploit agent 读它拿 API inventory / input vectors / 技术栈），缺失则 exploit 失明。
    TS 在 queue 校验前先校验 recon 存在，缺即 nonRetryable fail。此处只报告 has_recon_deliverable
    （单仓；corr 不补 recon——exploit agent 从单仓 wb_queue_root 读 recon，corr 只补 queue 候选），
    fail-fast 决策留 workflow（与 has_whitebox_results 同构）。
    """
    from supernova_core.utils.paths import (
        has_valid_whitebox_results,
        resolve_track_deliverable,
        WHITEBOX_SUBDIR,
    )

    dlv = Path(deliverables_path)
    found_classes = [
        vt for vt in vuln_classes
        if has_valid_whitebox_results(
            resolve_track_deliverable(dlv, WHITEBOX_SUBDIR, f"{vt}_exploitation_queue.json"))
    ]
    corr_classes: list[str] = []
    if correlation_deliverables_path and not found_classes:
        corr_dlv = Path(correlation_deliverables_path)
        corr_classes = [
            vt for vt in vuln_classes
            if has_valid_whitebox_results(
                resolve_track_deliverable(corr_dlv, WHITEBOX_SUBDIR, f"{vt}_exploitation_queue.json"))
        ]
    has_recon_deliverable = resolve_track_deliverable(
        dlv, WHITEBOX_SUBDIR, "recon_deliverable.md").exists()
    return {
        "has_whitebox_results": bool(found_classes or corr_classes),
        "found_classes": found_classes,
        "corr_classes": corr_classes,
        "has_recon_deliverable": has_recon_deliverable,
    }


@activity.defn
async def write_engine_config_for_session(
    repo_path: str, session_id: str, engine_name: str,
) -> None:
    """exploit 循环: 为每个 agent session 写浏览器 stealth config（文件 I/O，sandbox 禁）。

    engine_name 由 resolve_blackbox_engine 在 preflight 解析后经 workflow 透传——engine
    对象不可跨 workflow/activity 边界，故每次按 engine_name 重新 get_engine。
    write_config 幂等（wrote/skipped-existing），即便 exploit executor 内部也写无害。
    """
    from supernova_core.services.browser_engine import BrowserEngineFactory
    import supernova_core.services.engines  # noqa: F401 – registers engines

    engine = BrowserEngineFactory.get_engine(engine_name)
    engine.write_config(repo_path, session_id=session_id)


@activity.defn
async def cleanup_engine_configs(repo_path: str, engine_name: str) -> None:
    """finally 收尾: 清理各 session 的浏览器 stealth config（文件 I/O，sandbox 禁）。

    与 write_engine_config_for_session 对称——engine_name 由 resolve_blackbox_engine 在
    preflight 解析后经 workflow 透传，engine 对象不可跨 workflow/activity 边界故按 engine_name
    重新 get_engine。best-effort cleanup（write_config 幂等，残留 config 下次覆盖），失败由
    workflow 侧 try/except 吞掉不阻断收尾。session_id 集合取自 AGENT_SESSION_MAPPING（同 worker
    进程，get_session_id 在 workflow 侧填充）。
    """
    from supernova_core.services.browser_engine import BrowserEngineFactory
    from supernova_core.services.playwright_config_writer import AGENT_SESSION_MAPPING
    import supernova_core.services.engines  # noqa: F401 – registers engines

    engine = BrowserEngineFactory.get_engine(engine_name)
    session_ids = list(set(AGENT_SESSION_MAPPING.values()))
    # 进程清理先于 config 清理(config 删 profile 目录不影响杀进程匹配)
    try:
        engine.cleanup_processes(repo_path, session_ids=session_ids)
    except Exception:  # noqa: BLE001 - best-effort
        pass
    for session_id in session_ids:
        engine.cleanup_config(repo_path, session_id=session_id)
    engine.cleanup_config(repo_path)
