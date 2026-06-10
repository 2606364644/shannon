import asyncio
from datetime import timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError

from shannon_core.models.agents import AgentName, ALL_VULN_CLASSES, VulnType
from shannon_core.models.errors import ErrorCode, PentestError

from .shared import ActivityInput, PipelineInput, PipelineState, PipelineProgress

with workflow.unsafe.imports_passed_through():
    from . import activities
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
    from shannon_core.services.browser_engine import BrowserEngineFactory
    import shannon_core.services.engines  # noqa: F401 – registers engines
    from shannon_core.services.validate_authentication import cleanup_auth_state_sync
    from shannon_core.models.retry import (
        PREFLIGHT_RETRY, AUTH_VALIDATION_RETRY, PRODUCTION_RETRY, NON_RETRYABLE,
    )
    from shannon_core.models.errors import classify_error_for_temporal

@workflow.defn
class WhiteboxScanWorkflow:
    def __init__(self):
        self._state = PipelineState()

    @workflow.run
    async def run(self, input: PipelineInput) -> PipelineState:
        self._state.start_time = workflow.time_ns() / 1e9

        selected_classes: list[VulnType] = input.vuln_classes or list(ALL_VULN_CLASSES)

        # Compute workspace_path so activities know where to write auth-state.json
        if input.workspace_name:
            workspace_path = str(Path(input.repo_path).parent / "workspaces" / input.workspace_name)
        else:
            workspace_path = input.repo_path

        act_input = ActivityInput(
            repo_path=input.repo_path,
            web_url=input.web_url,
            config_path=input.config_path,
            workspace_name=input.workspace_name,
            deliverables_subdir=input.deliverables_subdir,
            pipeline_testing_mode=input.pipeline_testing_mode,
            api_key=input.api_key,
            prompt_override=input.prompt_override,
            workspace_path=workspace_path,
        )
        await workflow.execute_activity(
            activities.run_preflight, act_input,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=PREFLIGHT_RETRY,
        )

        # Credential check
        await workflow.execute_activity(
            activities.run_credential_check, act_input,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=PREFLIGHT_RETRY,
        )

        # Auth validation
        await workflow.execute_activity(
            activities.run_auth_validation, act_input,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=AUTH_VALIDATION_RETRY,
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

        # Write browser engine config (S5)
        engine.write_config(input.repo_path)

        try:
            # === Parallel: Code Index (deterministic) ∥ PRE_RECON (LLM) ===
            # These two have no data dependency. The original Shannon had no
            # deterministic layer, so PRE_RECON's Sink Hunter runs fine
            # without static-dataflow-hints.

            if AgentName.PRE_RECON.value not in self._state.completed_agents:
                self._state.current_phase = "pre-recon"
                self._state.current_agent = AgentName.PRE_RECON.value

                pre_recon_input = ActivityInput(
                    **{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value}
                )

                # Fail-fast: if either fails, cancel the other and propagate.
                code_index_result, pre_recon_metrics = await asyncio.gather(
                    workflow.execute_activity(
                        activities.run_code_index, act_input,
                        start_to_close_timeout=timedelta(minutes=10),
                    ),
                    workflow.execute_activity(
                        activities.run_agent, pre_recon_input,
                        start_to_close_timeout=timedelta(hours=2),
                        retry_policy=PRODUCTION_RETRY,
                    ),
                )

                self._state.code_index_stats = code_index_result
                self._state.completed_agents.append(AgentName.PRE_RECON.value)
                self._state.agent_metrics[AgentName.PRE_RECON.value] = pre_recon_metrics

                # Merge deterministic sinks with LLM-discovered sinks
                await workflow.execute_activity(
                    activities.run_merge_sink_reports, act_input,
                    start_to_close_timeout=timedelta(minutes=2),
                )

                # Entry point fusion: merge deterministic + LLM discoveries
                fusion_input = ActivityInput(
                    **{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value}
                )
                await workflow.execute_activity(
                    activities.run_entry_point_fusion, fusion_input,
                    start_to_close_timeout=timedelta(minutes=2),
                )

                # Adjudicate merged entry points by confidence
                adjudication_input = ActivityInput(
                    **{**act_input.__dict__, "workspace_name": AgentName.PRE_RECON.value}
                )
                await workflow.execute_activity(
                    activities.run_save_adjudication, adjudication_input,
                    start_to_close_timeout=timedelta(minutes=2),
                )
                self._state.current_agent = None

            if AgentName.RECON.value not in self._state.completed_agents:
                self._state.current_phase = "recon"
                self._state.current_agent = AgentName.RECON.value
                recon_input = ActivityInput(**{**act_input.__dict__, "workspace_name": AgentName.RECON.value})
                metrics = await workflow.execute_activity(
                    activities.run_agent, recon_input,
                    start_to_close_timeout=timedelta(hours=2),
                )
                self._state.completed_agents.append(AgentName.RECON.value)
                self._state.agent_metrics[AgentName.RECON.value] = metrics
                self._state.current_agent = None

            # Risk scoring — produce tiered audit plan
            risk_result = await workflow.execute_activity(
                activities.run_risk_scoring, act_input,
                start_to_close_timeout=timedelta(minutes=5),
            )
            self._state.audit_plan_stats = risk_result

            # Spec C: render static dataflow hints for vuln agents (after audit plan)
            await workflow.execute_activity(
                activities.run_render_dataflow_hints, act_input,
                start_to_close_timeout=timedelta(minutes=2),
            )

            self._state.current_phase = "vulnerability-analysis"
            vuln_tasks = []
            for vt in selected_classes:
                agent_name = AgentName(f"{vt}-vuln")
                if agent_name.value not in self._state.completed_agents:
                    self._state.current_agent = agent_name.value
                    vuln_input = ActivityInput(**{**act_input.__dict__, "workspace_name": agent_name.value})
                    vuln_tasks.append(
                        workflow.execute_activity(
                            activities.run_vuln_agent, vuln_input,
                            start_to_close_timeout=timedelta(hours=2),
                            retry_policy=RetryPolicy(
                                maximum_attempts=3,
                                initial_interval=timedelta(seconds=30),
                                maximum_interval=timedelta(minutes=5),
                                backoff_coefficient=2.0,
                                non_retryable_error_types=NON_RETRYABLE,
                            ),
                        )
                    )

            if vuln_tasks:
                results = await asyncio.gather(*vuln_tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    vt = selected_classes[i]
                    agent_name = AgentName(f"{vt}-vuln")
                    if isinstance(result, Exception):
                        self._state.errors.append(f"{agent_name.value}: {result}")
                        self._state.failed_agents.append(agent_name.value)
                    else:
                        self._state.completed_agents.append(agent_name.value)
                        self._state.agent_metrics[agent_name.value] = result

            self._state.current_phase = "reporting"
            self._state.current_agent = "render-findings"
            await workflow.execute_activity(
                activities.render_findings, act_input,
                start_to_close_timeout=timedelta(minutes=5),
            )
            self._state.current_agent = None

            # Set final status based on whether any agents failed
            if self._state.failed_agents:
                self._state.status = "failed"
                first_error_msg = self._state.errors[0].split(": ", 1)[-1] if self._state.errors else ""
                error_type, _ = classify_error_for_temporal(Exception(first_error_msg))
                self._state.error_code = error_type
            else:
                self._state.status = "completed"
            self._state.current_phase = None
            return self._state
        except CancelledError:
            self._state.status = "cancelled"
            self._state.current_phase = None
            return self._state
        finally:
            cleanup_settings()
            if engine:
                engine.cleanup_config(input.repo_path)
            cleanup_auth_state_sync(workspace_path)

    @workflow.query(name="PipelineProgress")
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
