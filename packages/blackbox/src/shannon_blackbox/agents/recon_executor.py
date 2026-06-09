from pathlib import Path
from typing import TYPE_CHECKING

from shannon_core.models.agents import AgentName
from shannon_core.models.metrics import AgentMetrics

from shannon_core.agents.executor import AgentExecutor

if TYPE_CHECKING:
    from shannon_core.logging.activity_logger import ActivityLogger


class ReconExecutor:
    def __init__(self, agent_executor: AgentExecutor):
        self._executor = agent_executor

    async def execute(
        self,
        workspace_path: Path,
        deliverables_path: Path,
        web_url: str,
        config_path: str | None = None,
        api_key: str | None = None,
        pipeline_testing: bool = False,
        audit_logger: "ActivityLogger | None" = None,
    ) -> AgentMetrics:
        return await self._executor.execute(
            agent_name=AgentName.RECON_BLACKBOX,
            repo_path=str(deliverables_path),
            web_url=web_url,
            deliverables_path=str(deliverables_path),
            config_path=config_path,
            api_key=api_key,
            pipeline_testing=pipeline_testing,
            audit_logger=audit_logger,
        )
