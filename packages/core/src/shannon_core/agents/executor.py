import json
import time
from pathlib import Path

from shannon_core.config.parser import distribute_config, parse_config
from shannon_core.models.agents import AgentName, AGENTS
from shannon_core.models.config import Config
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.models.metrics import AgentMetrics
from shannon_core.utils.billing import is_spending_cap_behavior

from shannon_core.agents.runner import ClaudeRunResult, run_claude_prompt
from shannon_core.agents.validators import get_queue_filename, get_vuln_type, validate_deliverable
from shannon_core.git_manager import GitManager
from shannon_core.prompts.manager import PromptManager

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
    ) -> AgentMetrics:
        defn = AGENTS[agent_name]
        repo = Path(repo_path)
        deliverables = Path(deliverables_path) if deliverables_path else repo / ".shannon" / "deliverables"
        deliverables.mkdir(parents=True, exist_ok=True)

        config: Config | None = None
        if config_path:
            config = parse_config(config_path)
        distributed = distribute_config(config)

        variables = {"web_url": web_url, "repo_path": str(repo)}
        if prompt_variables:
            variables.update(prompt_variables)
        template_name = prompt_override or defn.prompt_template
        prompt = self.prompt_manager.load_sync(
            template_name,
            variables=variables,
            config=distributed,
            pipeline_testing=pipeline_testing,
        )

        GitManager.create_checkpoint(deliverables, agent_name)

        start_time = time.monotonic()
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=str(repo),
            model_tier=defn.model_tier,
            api_key=api_key,
            deliverables_subdir=str(deliverables.relative_to(repo)) if deliverables.is_relative_to(repo) else None,
            structured_output_schema=structured_output_schema,
        )
        duration_ms = int((time.monotonic() - start_time) * 1000)

        if result.success and is_spending_cap_behavior(result.turns, result.cost, result.text):
            GitManager.rollback(deliverables, "spending cap detected")
            raise PentestError(
                f"Spending cap likely reached (turns={result.turns}, cost=${result.cost})",
                "billing",
                retryable=True,
                error_code=ErrorCode.SPENDING_CAP_REACHED,
            )

        if not result.success:
            GitManager.rollback(deliverables, "execution failure")
            raise PentestError(
                result.error or f"Agent {agent_name.value} execution failed",
                "validation",
                retryable=result.retryable,
                error_code=ErrorCode.AGENT_EXECUTION_FAILED,
            )

        queue_filename = get_queue_filename(agent_name)
        if result.structured_output is not None and queue_filename:
            queue_path = deliverables / queue_filename
            queue_path.write_text(json.dumps(result.structured_output, indent=2), encoding="utf-8")

        await validate_deliverable(deliverables, agent_name)

        GitManager.commit(deliverables, agent_name)

        return AgentMetrics(
            duration_ms=duration_ms,
            cost_usd=result.cost,
            num_turns=result.turns,
            model=result.model,
            structured_output=result.structured_output,
        )
