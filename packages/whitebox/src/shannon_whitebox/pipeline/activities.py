from datetime import timedelta
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationFailure

from shannon_core.models.agents import AgentName, AGENTS, ALL_VULN_CLASSES, VulnType
from shannon_core.models.errors import ErrorCode, PentestError, classify_error_for_temporal
from shannon_core.models.metrics import AgentMetrics
from shannon_core.utils.security import validate_target_url
from shannon_core.utils.paths import resolve_deliverables_path
from shannon_core.utils.credential_validator import validate_credentials
from shannon_core.agents.executor import AgentExecutor
from shannon_core.prompts.manager import PromptManager
from shannon_core.session import SessionManager
from shannon_whitebox.audit.session import AuditSession

from .shared import ActivityInput

def _get_paths(input: ActivityInput) -> tuple[Path, Path, Path]:
    deliverables = resolve_deliverables_path(
        repo_path=input.repo_path,
        deliverables_subdir=input.deliverables_subdir,
        workspace_name=input.workspace_name,
    )
    repo = Path(input.repo_path)
    workspaces = repo.parent / "workspaces"
    return repo, deliverables, workspaces

@activity.defn
async def run_preflight(input: ActivityInput) -> None:
    try:
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

        # URL safety check
        if input.web_url:
            validate_target_url(input.web_url)

        repo, _, _ = _get_paths(input)
        if not repo.exists():
            raise PentestError(
                f"Repository not found: {input.repo_path}",
                "config",
                error_code=ErrorCode.REPO_NOT_FOUND,
            )
        if not (repo / ".git").exists():
            raise PentestError(
                f"Not a git repository: {input.repo_path}",
                "config",
                error_code=ErrorCode.REPO_NOT_FOUND,
            )
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e

@activity.defn
async def run_agent(input: ActivityInput) -> dict:
    try:
        agent_name = AgentName(input.workspace_name)
        repo, deliverables, _ = _get_paths(input)
        prompt_manager = PromptManager(repo.parent.parent / "prompts")
        executor = AgentExecutor(prompt_manager)
        metrics = await executor.execute(
            agent_name=agent_name,
            repo_path=str(repo),
            web_url=input.web_url,
            deliverables_path=str(deliverables),
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
            prompt_override=input.prompt_override,
        )
        return metrics.model_dump()
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e

@activity.defn
async def run_vuln_agent(input: ActivityInput) -> dict:
    return await run_agent(input)


@activity.defn
async def run_credential_check(input: ActivityInput) -> None:
    try:
        import os
        provider = os.environ.get("SHANNON_AI_PROVIDER", "anthropic_api")
        # Priority: input.api_key > SHANNON_API_KEY > ANTHROPIC_API_KEY
        api_key = input.api_key or os.environ.get("SHANNON_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        base_url = os.environ.get("SHANNON_BASE_URL")
        if api_key or provider != "anthropic_api":
            await validate_credentials(provider, api_key=api_key, base_url=base_url)
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_auth_validation(input: ActivityInput) -> None:
    try:
        from shannon_core.services.validate_authentication import validate_authentication
        from shannon_core.prompts.manager import PromptManager
        from shannon_core.agents.executor import AgentExecutor

        prompts_dir = Path(__file__).resolve().parents[4] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)

        result = await validate_authentication(
            web_url=input.web_url,
            config_path=input.config_path,
            prompt_manager=prompt_manager,
            executor=executor,
            repo_path=input.repo_path,
            api_key=input.api_key,
            workspace_path=input.workspace_path or "",
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
async def run_code_index(input: ActivityInput) -> dict:
    try:
        from shannon_core.code_index import build_code_index, write_index_files

        repo, deliverables, _ = _get_paths(input)
        index = build_code_index(str(repo))
        json_path, summary_path = write_index_files(index, str(deliverables))

        return {
            "total_blocks": index.total_blocks,
            "total_entry_points": index.total_entry_points,
            "total_chains": index.total_chains,
            "json_path": str(json_path),
            "summary_path": str(summary_path),
        }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_rebuild_call_chains(input: ActivityInput) -> dict:
    try:
        from shannon_core.code_index import rebuild_call_chains

        repo, deliverables, _ = _get_paths(input)
        updated = rebuild_call_chains(str(deliverables))

        return {
            "total_blocks": updated.total_blocks,
            "total_entry_points": updated.total_entry_points,
            "total_chains": updated.total_chains,
        }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def render_findings(input: ActivityInput) -> None:
    try:
        from shannon_core.services.findings_renderer import FindingsRenderer
        from shannon_core.config.parser import parse_config

        _, deliverables, _ = _get_paths(input)
        report_config = None
        if input.config_path:
            cfg = parse_config(input.config_path)
            report_config = cfg.report
        await FindingsRenderer.render_findings_from_queues(deliverables, report_config)
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
