from pathlib import Path
from urllib.parse import urlparse

from temporalio import activity
from temporalio.exceptions import ApplicationError as ApplicationFailure

from shannon_core.models.agents import AgentName
from shannon_core.models.errors import ErrorCode, PentestError, classify_error_for_temporal
from shannon_core.utils.security import validate_target_url, check_url_reachable
from shannon_core.utils.credential_validator import validate_credentials
from shannon_core.agents.executor import AgentExecutor
from shannon_core.prompts.manager import PromptManager
from shannon_core.utils.paths import resolve_deliverables_path

from .shared import BlackboxActivityInput


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
    try:
        from shannon_core.services.validate_authentication import validate_authentication
        from shannon_core.prompts.manager import PromptManager
        from shannon_core.agents.executor import AgentExecutor

        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)

        result = await validate_authentication(
            web_url=input.web_url,
            config_path=input.config_path,
            workspace_path=input.workspace_path or "",
            prompt_manager=prompt_manager,
            executor=executor,
            repo_path=input.repo_path or "",
            api_key=input.api_key,
        )
        if not result.success:
            raise PentestError(
                f"Authentication validation failed: {result.failure_detail or 'unknown'}",
                category="preflight",
                retryable=False,
                error_code=ErrorCode.AUTH_LOGIN_FAILED,
            )
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_recon(input: BlackboxActivityInput) -> dict:
    try:
        from shannon_blackbox.agents.recon_executor import ReconExecutor

        deliverables = _get_deliverables_path(input)
        deliverables.mkdir(parents=True, exist_ok=True)
        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)
        recon = ReconExecutor(executor)
        metrics = await recon.execute(
            workspace_path=deliverables.parent,
            deliverables_path=deliverables,
            web_url=input.web_url,
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
        )
        return metrics.model_dump()
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_exploit_agent(input: BlackboxActivityInput) -> dict:
    try:
        from shannon_blackbox.agents.exploit_executor import ExploitExecutor

        vuln_type: str = input.vuln_type
        agent_name = AgentName(f"{vuln_type}-exploit")
        deliverables = _get_deliverables_path(input)
        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)
        exploit = ExploitExecutor(executor)
        metrics = await exploit.execute(
            agent_name=agent_name,
            vuln_type=vuln_type,
            workspace_path=deliverables.parent,
            deliverables_path=deliverables,
            web_url=input.web_url,
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
        )
        return metrics.model_dump()
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def assemble_report(input: BlackboxActivityInput) -> None:
    try:
        from shannon_blackbox.services.report_assembler import ReportAssembler
        from shannon_core.models.agents import ALL_VULN_CLASSES
        from shannon_core.services.findings_renderer import FindingsRenderer

        deliverables = _get_deliverables_path(input)
        vuln_classes: list[str] = list(ALL_VULN_CLASSES)
        report_path = deliverables / "comprehensive_security_assessment_report.md"

        report_config = None
        if input.config_path:
            from shannon_core.config.parser import parse_config
            cfg = parse_config(input.config_path)
            report_config = cfg.report
        await FindingsRenderer.render_findings_from_queues(deliverables, report_config)

        await ReportAssembler.assemble(deliverables, vuln_classes, report_path)
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_report_agent(input: BlackboxActivityInput) -> dict:
    try:
        deliverables = _get_deliverables_path(input)
        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)
        metrics = await executor.execute(
            agent_name=AgentName.REPORT,
            repo_path=str(deliverables),
            web_url=input.web_url,
            deliverables_path=str(deliverables),
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
        )
        return metrics.model_dump()
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def finalize_report(input: BlackboxActivityInput) -> None:
    try:
        from shannon_blackbox.services.report_assembler import ReportAssembler
        from shannon_core.interfaces.report_output_provider import NoOpReportOutputProvider

        deliverables = _get_deliverables_path(input)
        report_path = deliverables / "comprehensive_security_assessment_report.md"

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
