import asyncio
import logging
from datetime import timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.common import RetryPolicy

from shannon_core.models.agents import AgentName, ALL_VULN_CLASSES
from shannon_core.utils.paths import resolve_workspaces_dir, resolve_deliverables_path, has_valid_whitebox_results

from .shared import BlackboxActivityInput, BlackboxPipelineInput, BlackboxPipelineState

logger = logging.getLogger(__name__)

with workflow.unsafe.imports_passed_through():
    from . import activities
    from ..services.exploitation_checker import ExploitationChecker
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
    from shannon_core.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
    from shannon_core.services.validate_authentication import cleanup_auth_state_sync
    from shannon_core.models.retry import (
        PREFLIGHT_RETRY, AUTH_VALIDATION_RETRY, NON_RETRYABLE,
        get_retry_policy,
    )


@workflow.defn
class BlackboxScanWorkflow:
    def __init__(self):
        self._state = BlackboxPipelineState()

    @workflow.run
    async def run(self, input: BlackboxPipelineInput) -> BlackboxPipelineState:
        self._state.start_time = workflow.time_ns() / 1e9

        selected_classes: list[str] = input.vuln_classes or list(ALL_VULN_CLASSES)

        # Compute workspace_path consistent with whitebox (workspaces/<name>/)
        if input.workspace_name:
            workspace_path = str(resolve_workspaces_dir(input.repo_path) / input.workspace_name)
        else:
            workspace_path = input.repo_path

        act_input = BlackboxActivityInput(
            web_url=input.web_url,
            repo_path=input.repo_path,
            config_path=input.config_path,
            workspace_name=input.workspace_name,
            deliverables_subdir=input.deliverables_subdir,
            pipeline_testing_mode=input.pipeline_testing_mode,
            api_key=input.api_key,
            workspace_path=workspace_path,
        )

        retry_policy = get_retry_policy(
            "testing" if input.pipeline_testing_mode else (input.retry_profile or "production")
        )

        await workflow.execute_activity(
            activities.run_blackbox_preflight, act_input,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=PREFLIGHT_RETRY,
        )

        # Write code path deny rules (S6)
        if input.config_path:
            from shannon_core.config.parser import parse_config
            cfg = parse_config(input.config_path)
            if cfg.rules and cfg.rules.avoid:
                sync_code_path_deny_rules(cfg.rules.avoid)

        # Write stealth config (S5) — only if repo path provided
        if input.repo_path:
            write_stealth_config(input.repo_path)

        # Auth validation when config is present
        if input.config_path:
            await workflow.execute_activity(
                activities.run_blackbox_auth_validation, act_input,
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=AUTH_VALIDATION_RETRY,
            )

        try:
            # Resolve deliverables path using shared utility
            deliverables = resolve_deliverables_path(
                repo_path=input.repo_path,
                deliverables_subdir=input.deliverables_subdir,
                workspace_name=input.workspace_name,
                workspaces_root=resolve_workspaces_dir(input.repo_path),
            )

            has_whitebox_results = False
            found_classes: list[str] = []
            for vt in selected_classes:
                queue_file = deliverables / f"{vt}_exploitation_queue.json"
                if has_valid_whitebox_results(queue_file):
                    has_whitebox_results = True
                    found_classes.append(vt)
            self._state.has_whitebox_results = has_whitebox_results
            self._state.found_whitebox_classes = found_classes
            if has_whitebox_results:
                logger.info(
                    "Whitebox results detected at %s for classes: %s — skipping RECON_BLACKBOX",
                    deliverables,
                    found_classes,
                )
            else:
                logger.warning(
                    "No whitebox results found at %s — running RECON_BLACKBOX from scratch. "
                    "Tip: pass --repo <path> to reuse whitebox scan results.",
                    deliverables,
                )

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
                # Queue gating: validate queue files before scheduling exploit agents
                exploit_tasks = []
                for vt in selected_classes:
                    should_run = await ExploitationChecker.should_exploit(
                        deliverables_path=deliverables,
                        vuln_type=vt,
                        exploit_enabled=input.exploit,
                    )
                    if not should_run:
                        continue
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
                            self._state.errors.append(f"{agent_name.value}: {result}")
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

            await workflow.execute_activity(
                activities.finalize_report, act_input,
                start_to_close_timeout=timedelta(minutes=5),
            )

            self._state.status = "completed"
            return self._state
        finally:
            cleanup_settings()
            if input.repo_path:
                cleanup_stealth_config(input.repo_path)
                cleanup_auth_state_sync(act_input.workspace_path or input.repo_path)
