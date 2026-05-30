from datetime import timedelta
from pathlib import Path

from temporalio import activity

from shannon_core.models.agents import AgentName, AGENTS, ALL_VULN_CLASSES, VulnType
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.models.metrics import AgentMetrics
from shannon_core.utils.security import validate_target_url
from shannon_core.utils.credential_validator import validate_credentials
from shannon_core.agents.executor import AgentExecutor
from shannon_core.prompts.manager import PromptManager
from shannon_core.session import SessionManager
from shannon_whitebox.audit.session import AuditSession

from .shared import ActivityInput

def _get_paths(input: ActivityInput) -> tuple[Path, Path, Path]:
    repo = Path(input.repo_path)
    deliverables = repo / input.deliverables_subdir
    workspaces = repo.parent / "workspaces"
    return repo, deliverables, workspaces

@activity.defn
async def run_preflight(input: ActivityInput) -> None:
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

@activity.defn
async def run_agent(input: ActivityInput) -> dict:
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

@activity.defn
async def run_vuln_agent(input: ActivityInput) -> dict:
    return await run_agent(input)


@activity.defn
async def run_credential_check(input: ActivityInput) -> None:
    import os
    provider = os.environ.get("SHANNON_AI_PROVIDER", "anthropic_api")
    api_key = input.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if api_key or provider != "anthropic_api":
        await validate_credentials(provider, api_key=api_key)


@activity.defn
async def run_auth_validation(input: ActivityInput) -> None:
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
    )
    if not result.success:
        raise PentestError(
            f"Authentication validation failed: {result.failure_detail or 'unknown'}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.AUTH_LOGIN_FAILED,
        )
