from datetime import timedelta
from pathlib import Path

from temporalio import activity

from shannon_core.models.agents import AgentName, AGENTS, ALL_VULN_CLASSES, VulnType
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.models.metrics import AgentMetrics
from shannon_whitebox.agents.executor import AgentExecutor
from shannon_whitebox.prompts.manager import PromptManager
from shannon_whitebox.session import SessionManager
from shannon_whitebox.audit.session import AuditSession

from .shared import ActivityInput

def _get_paths(input: ActivityInput) -> tuple[Path, Path, Path]:
    repo = Path(input.repo_path)
    deliverables = repo / input.deliverables_subdir
    workspaces = repo.parent / "workspaces"
    return repo, deliverables, workspaces

@activity.defn
async def run_preflight(input: ActivityInput) -> None:
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
    )
    return metrics.model_dump()

@activity.defn
async def run_vuln_agent(input: ActivityInput) -> dict:
    return await run_agent(input)
