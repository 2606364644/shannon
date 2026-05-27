from pathlib import Path

from temporalio import activity

from shannon_core.models.agents import AgentName
from shannon_whitebox.agents.executor import AgentExecutor
from shannon_whitebox.prompts.manager import PromptManager

from .shared import BlackboxActivityInput


def _get_deliverables_path(input: BlackboxActivityInput) -> Path:
    if input.repo_path:
        return Path(input.repo_path) / input.deliverables_subdir
    base = Path("workspaces") / (input.workspace_name or "default")
    return base / input.deliverables_subdir


@activity.defn
async def run_blackbox_preflight(input: BlackboxActivityInput) -> None:
    pass


@activity.defn
async def run_recon(input: BlackboxActivityInput) -> dict:
    from shannon_blackbox.agents.recon_executor import ReconExecutor

    deliverables = _get_deliverables_path(input)
    deliverables.mkdir(parents=True, exist_ok=True)
    prompts_dir = Path(__file__).resolve().parents[4] / "prompts"
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


@activity.defn
async def run_exploit_agent(input: BlackboxActivityInput) -> dict:
    from shannon_blackbox.agents.exploit_executor import ExploitExecutor

    vuln_type: str = input.vuln_type
    agent_name = AgentName(f"{vuln_type}-exploit")
    deliverables = _get_deliverables_path(input)
    prompts_dir = Path(__file__).resolve().parents[4] / "prompts"
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


@activity.defn
async def assemble_report(input: BlackboxActivityInput) -> None:
    from shannon_blackbox.services.report_assembler import ReportAssembler

    deliverables = _get_deliverables_path(input)
    vuln_classes: list[str] = ["injection", "xss", "auth", "ssrf", "authz"]
    report_path = deliverables / "comprehensive_security_assessment_report.md"
    await ReportAssembler.assemble(deliverables, vuln_classes, report_path)


@activity.defn
async def run_report_agent(input: BlackboxActivityInput) -> dict:
    deliverables = _get_deliverables_path(input)
    prompts_dir = Path(__file__).resolve().parents[4] / "prompts"
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
