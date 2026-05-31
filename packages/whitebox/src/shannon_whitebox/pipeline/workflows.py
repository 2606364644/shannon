import asyncio
from datetime import timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.common import RetryPolicy

from shannon_core.models.agents import AgentName, ALL_VULN_CLASSES, VulnType
from shannon_core.models.errors import PentestError

from .shared import ActivityInput, PipelineInput, PipelineState

with workflow.unsafe.imports_passed_through():
    from . import activities
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
    from shannon_core.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
    from shannon_core.services.validate_authentication import cleanup_auth_state_sync

@workflow.defn
class WhiteboxScanWorkflow:
    def __init__(self):
        self._state = PipelineState()

    @workflow.run
    async def run(self, input: PipelineInput) -> PipelineState:
        self._state.start_time = workflow.time_ns() / 1e9

        selected_classes: list[VulnType] = input.vuln_classes or list(ALL_VULN_CLASSES)

        act_input = ActivityInput(
            repo_path=input.repo_path,
            web_url=input.web_url,
            config_path=input.config_path,
            workspace_name=input.workspace_name,
            deliverables_subdir=input.deliverables_subdir,
            pipeline_testing_mode=input.pipeline_testing_mode,
            api_key=input.api_key,
            prompt_override=input.prompt_override,
        )
        await workflow.execute_activity(
            activities.run_preflight, act_input,
            start_to_close_timeout=timedelta(minutes=2),
        )

        # Credential check
        await workflow.execute_activity(
            activities.run_credential_check, act_input,
            start_to_close_timeout=timedelta(minutes=1),
        )

        # Auth validation
        await workflow.execute_activity(
            activities.run_auth_validation, act_input,
            start_to_close_timeout=timedelta(minutes=5),
        )

        # Write code path deny rules (S6)
        if input.config_path:
            from shannon_core.config.parser import parse_config
            cfg = parse_config(input.config_path)
            if cfg.rules and cfg.rules.avoid:
                sync_code_path_deny_rules(cfg.rules.avoid)

        # Write stealth config (S5)
        write_stealth_config(input.repo_path)

        try:
            if AgentName.PRE_RECON.value not in self._state.completed_agents:
                pre_recon_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value})
                metrics = await workflow.execute_activity(
                    activities.run_agent, pre_recon_input,
                    start_to_close_timeout=timedelta(hours=2),
                    retry_policy=RetryPolicy(
                        maximum_attempts=50,
                        initial_interval=timedelta(minutes=5),
                        maximum_interval=timedelta(minutes=30),
                        backoff_coefficient=2.0,
                    ),
                )
                self._state.completed_agents.append(AgentName.PRE_RECON.value)
                self._state.agent_metrics[AgentName.PRE_RECON.value] = metrics

            if AgentName.RECON.value not in self._state.completed_agents:
                recon_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.RECON.value})
                metrics = await workflow.execute_activity(
                    activities.run_agent, recon_input,
                    start_to_close_timeout=timedelta(hours=2),
                )
                self._state.completed_agents.append(AgentName.RECON.value)
                self._state.agent_metrics[AgentName.RECON.value] = metrics

            vuln_tasks = []
            for vt in selected_classes:
                agent_name = AgentName(f"{vt}-vuln")
                if agent_name.value not in self._state.completed_agents:
                    vuln_input = ActivityInput(**{**act_input.__dict__, "workspace_name": agent_name.value})
                    vuln_tasks.append(
                        workflow.execute_activity(
                            activities.run_vuln_agent, vuln_input,
                            start_to_close_timeout=timedelta(hours=2),
                        )
                    )

            if vuln_tasks:
                results = await asyncio.gather(*vuln_tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    vt = selected_classes[i]
                    agent_name = AgentName(f"{vt}-vuln")
                    if isinstance(result, Exception):
                        self._state.error = f"{agent_name.value}: {result}"
                    else:
                        self._state.completed_agents.append(agent_name.value)
                        self._state.agent_metrics[agent_name.value] = result

            self._state.status = "completed"
            return self._state
        finally:
            cleanup_settings()
            cleanup_stealth_config(input.repo_path)
            if input.workspace_name:
                ws_path = str(Path(input.repo_path).parent / "workspaces" / input.workspace_name)
            else:
                ws_path = input.repo_path
            cleanup_auth_state_sync(ws_path)
