import asyncio
import logging
from datetime import timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError

from shannon_core.models.agents import AgentName, ALL_VULN_CLASSES
from shannon_core.utils.paths import resolve_workspaces_dir, resolve_deliverables_path, has_valid_whitebox_results

from .shared import BlackboxActivityInput, BlackboxPipelineInput, BlackboxPipelineState, PipelineProgress

logger = logging.getLogger(__name__)

with workflow.unsafe.imports_passed_through():
    from . import activities
    from ..services.exploitation_checker import ExploitationChecker
    from shannon_core.utils.progress import AgentOutcome, format_exploit_summary
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
    from shannon_core.services.browser_engine import BrowserEngineFactory
    import shannon_core.services.engines  # noqa: F401 – registers engines
    from shannon_core.services.playwright_config_writer import (
        get_session_id,
        AGENT_SESSION_MAPPING,
    )
    from shannon_core.services.validate_authentication import cleanup_auth_state_sync
    from shannon_core.models.retry import (
        PREFLIGHT_RETRY, AUTH_VALIDATION_RETRY, NON_RETRYABLE,
        get_retry_policy,
    )
    from shannon_core.models.errors import PentestError, ErrorCode, classify_error_for_temporal


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

        # Resolve config and browser engine
        cfg = None
        engine = None
        if input.config_path:
            from shannon_core.config.parser import parse_config
            cfg = parse_config(input.config_path)

        engine_name = cfg.browser_engine if cfg else "playwright"
        try:
            engine = BrowserEngineFactory.get_engine(engine_name)
        except KeyError as e:
            raise PentestError(
                f"No browser engine registered as '{engine_name}'.",
                "browser",
                error_code=ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
            ) from e
        if not engine.check_available():
            raise PentestError(
                f"Browser engine '{engine.name}' is not available. "
                f"Install it with: npm install -g {engine.name} && {engine.name} install",
                "browser",
                error_code=ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
            )

        # Write code path deny rules (S6)
        if cfg and cfg.rules and cfg.rules.avoid:
            sync_code_path_deny_rules(cfg.rules.avoid)

        # Write browser engine config (S5) — only if repo path provided
        if input.repo_path:
            engine.write_config(input.repo_path)

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
                validation_results = []
                exploit_tasks = []
                for vt in selected_classes:
                    validation = await ExploitationChecker.validate_queue(
                        deliverables_path=deliverables,
                        vuln_type=vt,
                    )
                    validation_results.append((vt, validation))
                    if not validation.valid:
                        if validation.is_expected:
                            logger.debug(
                                "Skipping exploit for %s (expected): %s",
                                vt, validation.message,
                            )
                        else:
                            logger.warning(
                                "Skipping exploit for %s (anomalous): %s | queue_path=%s",
                                vt,
                                validation.message,
                                validation.context.get("queue_path", "N/A"),
                            )
                        continue
                    agent_name = AgentName(f"{vt}-exploit")
                    if agent_name.value not in self._state.completed_agents:
                        session_id = get_session_id(agent_name.value)
                        engine.write_config(input.repo_path, session_id=session_id)
                        exploit_input = BlackboxActivityInput(
                            **{**act_input.__dict__, "agent_name": agent_name.value, "vuln_type": vt}
                        )
                        exploit_tasks.append((vt, agent_name, workflow.execute_activity(
                            activities.run_exploit_agent, exploit_input,
                            start_to_close_timeout=timedelta(hours=2),
                            retry_policy=retry_policy,
                        )))

                # Validation summary log
                _VALIDATION_ICONS = {"valid": "✅", "expected": "⏭️", "anomalous": "⚠️"}
                summary_lines = ["Validation summary:"]
                for vt, v in validation_results:
                    if v.valid:
                        icon = _VALIDATION_ICONS["valid"]
                    elif v.is_expected:
                        icon = _VALIDATION_ICONS["expected"]
                    else:
                        icon = _VALIDATION_ICONS["anomalous"]
                    summary_lines.append(f"  {icon} {vt}: {v.message}")
                logger.info("\n".join(summary_lines))

                # Track scheduled vuln types for skipped outcomes
                scheduled_vuln_types = {vt for vt, _ in exploit_tasks}

                if exploit_tasks:
                    semaphore = asyncio.Semaphore(input.max_concurrent)

                    async def bounded_exploit(
                        coro, vt: str, agent_name: AgentName
                    ):
                        async with semaphore:
                            return await coro

                    results = await asyncio.gather(
                        *[bounded_exploit(task, vt, agent_name) for vt, agent_name, task in exploit_tasks],
                        return_exceptions=True,
                    )

                    # Build AgentOutcome list from results
                    outcomes: list[AgentOutcome] = []
                    for i, result in enumerate(results):
                        vt, agent_name, _ = exploit_tasks[i]
                        if isinstance(result, Exception):
                            self._state.errors.append(f"{agent_name.value}: {result}")
                            self._state.failed_agents.append(agent_name.value)
                            outcomes.append(AgentOutcome(
                                agent_name=agent_name.value,
                                vuln_type=vt,
                                status="failed",
                                error=str(result),
                            ))
                        else:
                            self._state.completed_agents.append(agent_name.value)
                            self._state.agent_metrics[agent_name.value] = result
                            # Extract metrics from result if available
                            duration_s = getattr(result, "duration_s", 0.0)
                            cost_usd = getattr(result, "cost_usd", 0.0)
                            turns = getattr(result, "turns", 0)
                            outcomes.append(AgentOutcome(
                                agent_name=agent_name.value,
                                vuln_type=vt,
                                status="completed",
                                duration_s=duration_s,
                                cost_usd=cost_usd,
                                turns=turns,
                            ))

                    # Add skipped outcomes for vuln types that were not scheduled
                    for vt, validation in validation_results:
                        if vt not in scheduled_vuln_types:
                            outcomes.append(AgentOutcome(
                                agent_name=f"{vt}-exploit",
                                vuln_type=vt,
                                status="skipped",
                            ))

                    logger.info(format_exploit_summary(outcomes))

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

            # Set final status based on failure tracking
            if self._state.failed_agents:
                self._state.status = "failed"
                first_error_msg = self._state.errors[0].split(": ", 1)[-1] if self._state.errors else ""
                error_type, _ = classify_error_for_temporal(Exception(first_error_msg))
                self._state.error_code = error_type
            else:
                self._state.status = "completed"
            return self._state
        except CancelledError:
            self._state.status = "cancelled"
            return self._state
        finally:
            cleanup_settings()
            if engine and input.repo_path:
                # Clean up session-specific configs
                for session_id in set(AGENT_SESSION_MAPPING.values()):
                    engine.cleanup_config(input.repo_path, session_id=session_id)
                engine.cleanup_config(input.repo_path)
            cleanup_auth_state_sync(act_input.workspace_path or input.repo_path)

    @workflow.query
    def pipeline_progress(self) -> PipelineProgress:
        """返回当前工作流进度供 CLI 轮询。"""
        elapsed_ns = workflow.time_ns() - int(self._state.start_time * 1e9)
        return PipelineProgress(
            workflow_id=workflow.info().workflow_id,
            elapsed_ms=elapsed_ns // 1_000_000,
            current_phase=self._state.current_phase,
            current_agent=self._state.current_agent,
            completed_agents=self._state.completed_agents,
            status=self._state.status,
        )
