import asyncio
from datetime import timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.common import RetryPolicy

from shannon_core.models.agents import AgentName, ALL_VULN_CLASSES

from .shared import BlackboxActivityInput, BlackboxPipelineInput, BlackboxPipelineState

with workflow.unsafe.imports_passed_through():
    from . import activities


@workflow.defn
class BlackboxScanWorkflow:
    def __init__(self):
        self._state = BlackboxPipelineState()

    @workflow.run
    async def run(self, input: BlackboxPipelineInput) -> BlackboxPipelineState:
        self._state.start_time = workflow.time_ns() / 1e9

        selected_classes: list[str] = input.vuln_classes or list(ALL_VULN_CLASSES)

        act_input = BlackboxActivityInput(
            web_url=input.web_url,
            repo_path=input.repo_path,
            config_path=input.config_path,
            workspace_name=input.workspace_name,
            deliverables_subdir=input.deliverables_subdir,
            pipeline_testing_mode=input.pipeline_testing_mode,
            api_key=input.api_key,
        )

        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=30),
            maximum_interval=timedelta(minutes=5),
            backoff_coefficient=2.0,
        )

        await workflow.execute_activity(
            activities.run_blackbox_preflight, act_input,
            start_to_close_timeout=timedelta(minutes=2),
        )

        deliverables = Path(input.repo_path or "") / input.deliverables_subdir if input.repo_path else Path("workspaces") / (input.workspace_name or "default") / input.deliverables_subdir
        has_whitebox_results = False
        for vt in selected_classes:
            queue_file = deliverables / f"{vt}_exploitation_queue.json"
            if queue_file.exists():
                has_whitebox_results = True
                break
        self._state.has_whitebox_results = has_whitebox_results

        if not has_whitebox_results and AgentName.RECON_BLACKBOX.value not in self._state.completed_agents:
            recon_input = BlackboxActivityInput(**{**act_input.__dict__})
            metrics = await workflow.execute_activity(
                activities.run_recon, recon_input,
                start_to_close_timeout=timedelta(hours=2),
                retry_policy=retry_policy,
            )
            self._state.completed_agents.append(AgentName.RECON_BLACKBOX.value)
            self._state.agent_metrics[AgentName.RECON_BLACKBOX.value] = metrics

        if input.exploit:
            exploit_tasks = []
            for vt in selected_classes:
                agent_name = AgentName(f"{vt}-exploit")
                if agent_name.value not in self._state.completed_agents:
                    exploit_input = BlackboxActivityInput(
                        **{**act_input.__dict__, "agent_name": agent_name.value, "vuln_type": vt}
                    )
                    exploit_tasks.append((vt, agent_name, workflow.execute_activity(
                        activities.run_exploit_agent, exploit_input,
                        start_to_close_timeout=timedelta(hours=2),
                        retry_policy=retry_policy,
                    )))

            if exploit_tasks:
                results = await asyncio.gather(
                    *[task for _, _, task in exploit_tasks],
                    return_exceptions=True,
                )
                for i, result in enumerate(results):
                    vt, agent_name, _ = exploit_tasks[i]
                    if isinstance(result, Exception):
                        self._state.error = f"{agent_name.value}: {result}"
                    else:
                        self._state.completed_agents.append(agent_name.value)
                        self._state.agent_metrics[agent_name.value] = result

        await workflow.execute_activity(
            activities.assemble_report, act_input,
            start_to_close_timeout=timedelta(minutes=5),
        )

        if AgentName.REPORT.value not in self._state.completed_agents:
            metrics = await workflow.execute_activity(
                activities.run_report_agent, act_input,
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=retry_policy,
            )
            self._state.completed_agents.append(AgentName.REPORT.value)
            self._state.agent_metrics[AgentName.REPORT.value] = metrics

        self._state.status = "completed"
        return self._state
