import time
from pathlib import Path
from urllib.parse import urlparse

from temporalio import activity
from temporalio.exceptions import ApplicationError as ApplicationFailure

from shannon_core.models.agents import AgentName
from shannon_core.models.errors import ErrorCode, PentestError, classify_error_for_temporal
from shannon_core.models.retry import agent_retry_category, retry_for
from shannon_core.utils.security import validate_target_url, check_url_reachable
from shannon_core.utils.credential_validator import validate_credentials
from shannon_core.agents.executor import AgentExecutor
from shannon_core.prompts.manager import PromptManager
from shannon_core.utils.paths import (
    BLACKBOX_SUBDIR,
    WHITEBOX_SUBDIR,
    blackbox_dir,
    resolve_deliverables_path,
)

from .shared import BlackboxActivityInput
from shannon_blackbox.services.exploitation_checker import QueueValidationResult


def _get_deliverables_path(input: BlackboxActivityInput) -> Path:
    return resolve_deliverables_path(
        repo_path=input.repo_path,
        deliverables_subdir=input.deliverables_subdir,
        workspace_name=input.workspace_name,
    )


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
            from shannon_core.config.parser import parse_config
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
    from shannon_core.audit.session_registry import get_audit_session
    from shannon_core.audit.session_tool_audit_logger import SessionToolAuditLogger
    from shannon_core.models.audit import AgentEndResult
    from shannon_core.models.agents import AgentName

    agent_name = AgentName.VALIDATE_AUTH
    attempt = activity.info().attempt
    max_attempts = retry_for(agent_retry_category(agent_name.value)).maximum_attempts
    session = get_audit_session()
    tool_audit_logger = SessionToolAuditLogger(session, agent_name.value, attempt)
    agent_start = time.monotonic()
    try:
        from shannon_core.services.validate_authentication import validate_authentication

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
async def run_recon(input: BlackboxActivityInput) -> dict:
    from shannon_core.audit.session_registry import get_audit_session
    from shannon_core.audit.session_tool_audit_logger import SessionToolAuditLogger
    from shannon_core.models.audit import AgentEndResult

    agent_name = AgentName.RECON_BLACKBOX
    attempt = activity.info().attempt
    max_attempts = retry_for(agent_retry_category(agent_name.value)).maximum_attempts
    session = get_audit_session()
    tool_audit_logger = SessionToolAuditLogger(session, agent_name.value, attempt)
    agent_start = time.monotonic()
    try:
        from shannon_blackbox.agents.recon_executor import ReconExecutor

        deliverables = _get_deliverables_path(input)
        deliverables.mkdir(parents=True, exist_ok=True)
        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)
        recon = ReconExecutor(executor)

        await session.start_agent(agent_name.value, f"agent={agent_name.value}", attempt=attempt)
        await tool_audit_logger.initialize()
        metrics = await recon.execute(
            workspace_path=deliverables.parent,
            deliverables_path=deliverables,
            web_url=input.web_url,
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
async def run_exploit_agent(input: BlackboxActivityInput) -> dict:
    from shannon_core.audit.session_registry import get_audit_session
    from shannon_core.audit.session_tool_audit_logger import SessionToolAuditLogger
    from shannon_core.models.audit import AgentEndResult

    vuln_type: str = input.vuln_type
    agent_name = AgentName(f"{vuln_type}-exploit")
    attempt = activity.info().attempt
    max_attempts = retry_for(agent_retry_category(agent_name.value)).maximum_attempts
    session = get_audit_session()
    tool_audit_logger = SessionToolAuditLogger(session, agent_name.value, attempt)
    agent_start = time.monotonic()
    try:
        from shannon_blackbox.agents.exploit_executor import ExploitExecutor

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
async def assemble_report(input: BlackboxActivityInput) -> None:
    try:
        from shannon_core.services.report_assembler import ReportAssembler
        from shannon_core.models.agents import ALL_VULN_CLASSES
        from shannon_core.services.findings_renderer import FindingsRenderer

        deliverables = _get_deliverables_path(input)
        vuln_classes: list[str] = list(ALL_VULN_CLASSES)
        bb = blackbox_dir(deliverables)
        bb.mkdir(parents=True, exist_ok=True)
        report_path = bb / "comprehensive_security_assessment_report.md"

        report_config = None
        if input.config_path:
            from shannon_core.config.parser import parse_config
            cfg = parse_config(input.config_path)
            report_config = cfg.report
        await FindingsRenderer.render_findings_from_queues(
            deliverables, report_config,
            queue_subdir=WHITEBOX_SUBDIR, findings_subdir=BLACKBOX_SUBDIR)

        # AU-6: exploit queue→evidence 闭环——未覆盖条目写入 evidence 未覆盖节，
        # ReportAssembler 读 evidence 全文时自动带入最终报告。
        from shannon_blackbox.services.coverage_renderer import close_coverage_gaps
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
    from shannon_blackbox.services.exploitation_checker import ExploitationChecker
    try:
        deliverables = _get_deliverables_path(input)
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
    from shannon_core.audit.session_registry import get_audit_session
    from shannon_core.audit.session_tool_audit_logger import SessionToolAuditLogger
    from shannon_core.models.audit import AgentEndResult

    agent_name = AgentName.REPORT
    attempt = activity.info().attempt
    max_attempts = retry_for(agent_retry_category(agent_name.value)).maximum_attempts
    session = get_audit_session()
    tool_audit_logger = SessionToolAuditLogger(session, agent_name.value, attempt)
    agent_start = time.monotonic()
    try:
        deliverables = _get_deliverables_path(input)
        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)

        await session.start_agent(agent_name.value, f"agent={agent_name.value}", attempt=attempt)
        await tool_audit_logger.initialize()
        metrics = await executor.execute(
            agent_name=agent_name,
            repo_path=str(deliverables),
            web_url=input.web_url,
            deliverables_path=str(deliverables),
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
        from shannon_core.services.report_assembler import ReportAssembler
        from shannon_core.interfaces.report_output_provider import NoOpReportOutputProvider

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
async def generate_poc_report(input: BlackboxActivityInput) -> None:
    """报告增强：生成 curl/Burp PoC md（黑盒）。失败不阻塞主报告。"""
    import logging
    log = logging.getLogger(__name__)
    try:
        from shannon_core.services.poc_generator import PoCGenerator
        from shannon_core.models.config import ALL_VULN_CLASSES
        from shannon_core.utils.paths import blackbox_dir

        deliverables = _get_deliverables_path(input)
        bb = blackbox_dir(deliverables)
        bb.mkdir(parents=True, exist_ok=True)
        await PoCGenerator.generate(
            deliverables_dir=bb,
            vuln_classes=list(ALL_VULN_CLASSES),
            target_url=(input.web_url or None),
            track="blackbox",
            repo_path=input.repo_path,
            api_key=input.api_key,
        )
    except Exception as exc:  # noqa: BLE001 — 报告增强失败绝不阻塞主流程
        log.warning("poc: blackbox generate_poc_report failed (non-blocking): %s", exc)


@activity.defn
async def log_phase_start_activity(input: BlackboxActivityInput) -> None:
    from shannon_core.audit.session_registry import get_audit_session
    phase = input.phase or input.workspace_name or "unknown"
    await get_audit_session().log_phase_start(phase)


@activity.defn
async def log_phase_complete_activity(input: BlackboxActivityInput) -> None:
    from shannon_core.audit.session_registry import get_audit_session
    phase = input.phase or input.workspace_name or "unknown"
    await get_audit_session().log_phase_complete(phase)


@activity.defn
async def log_info_activity(input: BlackboxActivityInput) -> None:
    from shannon_core.audit.session_registry import get_audit_session
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
        from shannon_core.config.parser import parse_config
        from shannon_core.services.settings_writer import sync_code_path_deny_rules
        from shannon_core.services.browser_engine import BrowserEngineFactory
        import shannon_core.services.engines  # noqa: F401 – registers engines

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
    返回 {has_whitebox_results, found_classes, corr_classes}。
    """
    from shannon_core.utils.paths import (
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
    return {
        "has_whitebox_results": bool(found_classes or corr_classes),
        "found_classes": found_classes,
        "corr_classes": corr_classes,
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
    from shannon_core.services.browser_engine import BrowserEngineFactory
    import shannon_core.services.engines  # noqa: F401 – registers engines

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
    from shannon_core.services.browser_engine import BrowserEngineFactory
    from shannon_core.services.playwright_config_writer import AGENT_SESSION_MAPPING
    import shannon_core.services.engines  # noqa: F401 – registers engines

    engine = BrowserEngineFactory.get_engine(engine_name)
    for session_id in set(AGENT_SESSION_MAPPING.values()):
        engine.cleanup_config(repo_path, session_id=session_id)
    engine.cleanup_config(repo_path)
