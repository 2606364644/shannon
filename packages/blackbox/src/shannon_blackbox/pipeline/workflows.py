import asyncio
import logging
from datetime import timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.common import RetryPolicy

from shannon_core.models.agents import AgentName, ALL_VULN_CLASSES

from .shared import BlackboxActivityInput, BlackboxPipelineInput, BlackboxPipelineState

logger = logging.getLogger(__name__)

with workflow.unsafe.imports_passed_through():
    from . import activities
    from ..services.exploitation_checker import ExploitationChecker
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
    from shannon_core.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
    from shannon_core.services.validate_authentication import cleanup_auth_state_sync


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
            workspace_path=input.repo_path,
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
                start_to_close_timeout=timedelta(minutes=5),
            )

        try:
            # Resolve deliverables path: prefer explicit repo_path, fall back to session data, then default
            deliverables = None
            if input.repo_path:
                deliverables = Path(input.repo_path) / input.deliverables_subdir
            elif input.workspace_name:
                session_file = Path("workspaces") / input.workspace_name / "session.json"
                if session_file.exists():
                    import json as _json
                    session_data = _json.loads(session_file.read_text())
                    saved_repo = session_data.get("repo_path")
                    if saved_repo:
                        deliverables = Path(saved_repo) / input.deliverables_subdir
            if not deliverables:
                deliverables = Path("workspaces") / (input.workspace_name or "default") / input.deliverables_subdir

            has_whitebox_results = False
            found_classes: list[str] = []
            for vt in selected_classes:
                queue_file = deliverables / f"{vt}_exploitation_queue.json"
                if queue_file.exists():
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

            self._state.status = "completed"
            return self._state
        finally:
            cleanup_settings()
            if input.repo_path:
                cleanup_stealth_config(input.repo_path)
                cleanup_auth_state_sync(act_input.workspace_path or input.repo_path)
