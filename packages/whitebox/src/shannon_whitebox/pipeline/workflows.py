import asyncio
from datetime import timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.exceptions import CancelledError

from shannon_core.models.agents import AgentName, ALL_VULN_CLASSES, VulnType
from shannon_core.models.errors import ErrorCode, PentestError

from .shared import ActivityInput, PipelineInput, PipelineState, PipelineProgress
from .step_intents import step_names, step_intents


def vuln_phase_steps(vuln_classes: list[str]) -> tuple[str, ...]:
    return tuple(f"{vt}-vuln" for vt in vuln_classes)


with workflow.unsafe.imports_passed_through():
    from . import activities
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
    from shannon_core.services.browser_engine import BrowserEngineFactory
    import shannon_core.services.engines  # noqa: F401 – registers engines
    from shannon_core.services.validate_authentication import cleanup_auth_state_sync
    from shannon_core.models.retry import retry_for
    from shannon_core.models.errors import classify_error_for_temporal

@workflow.defn
class WhiteboxScanWorkflow:
    def __init__(self):
        self._state = PipelineState()

    @workflow.run
    async def run(self, input: PipelineInput) -> PipelineState:
        # resume: 预填已完成 agent，激活下方 `if X not in completed_agents` 守卫
        if input.resume_completed_agents:
            self._state.completed_agents = list(input.resume_completed_agents)
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
            activities.log_phase_start_activity,
            args=[
                ActivityInput(**{**act_input.__dict__, "phase": "setup"}),
                list(step_names("setup")),
                list(step_intents("setup")),
            ],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=retry_for("log"),
        )
        self._state.current_phase = "setup"
        await workflow.execute_activity(
            activities.run_preflight, act_input,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry_for("preflight"),
        )

        # Credential check
        await workflow.execute_activity(
            activities.run_credential_check, act_input,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=retry_for("preflight"),
        )

        # Auth validation
        await workflow.execute_activity(
            activities.run_auth_validation, act_input,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_for("auth-validation"),
        )
        await workflow.execute_activity(
            activities.log_phase_complete_activity,
            ActivityInput(**{**act_input.__dict__, "phase": "setup"}),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=retry_for("log"),
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
            # on its own.

            if AgentName.PRE_RECON.value not in self._state.completed_agents:
                await workflow.execute_activity(
                    activities.log_phase_start_activity,
                    args=[
                        ActivityInput(**{**act_input.__dict__, "phase": "pre-recon"}),
                        list(step_names("pre-recon")),
                        list(step_intents("pre-recon")),
                    ],
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
                self._state.current_phase = "pre-recon"
                self._state.current_agent = AgentName.PRE_RECON.value

                # Fail-fast: if either fails, cancel the other and propagate.
                code_index_result, pre_recon_metrics = await asyncio.gather(
                    workflow.execute_activity(
                        activities.run_code_index, act_input,
                        start_to_close_timeout=timedelta(minutes=10),
                        retry_policy=retry_for("standard"),
                    ),
                    workflow.execute_activity(
                        activities.run_agent,
                        ActivityInput(**{**act_input.__dict__, "agent_name": AgentName.PRE_RECON.value}),
                        start_to_close_timeout=timedelta(hours=2),
                        retry_policy=retry_for("standard"),
                    ),
                )

                self._state.code_index_stats = code_index_result
                self._state.completed_agents.append(AgentName.PRE_RECON.value)
                self._state.agent_metrics[AgentName.PRE_RECON.value] = pre_recon_metrics

                if input.enable_llm_track:
                    # Merge deterministic sinks with LLM-discovered sinks (needs LLM deliverable)
                    await workflow.execute_activity(
                        activities.run_merge_sink_reports, act_input,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=retry_for("standard"),
                    )

                    # Entry point fusion: merge deterministic + LLM discoveries (needs LLM deliverable)
                    await workflow.execute_activity(
                        activities.run_entry_point_fusion, act_input,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=retry_for("standard"),
                    )

                # Adjudicate merged entry points by confidence
                await workflow.execute_activity(
                    activities.run_save_adjudication, act_input,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=retry_for("standard"),
                )
                self._state.current_agent = None

                # === Route Analysis Phase (parallel) ===
                framework_result, frontend_result = await asyncio.gather(
                    workflow.execute_activity(
                        activities.run_framework_analysis, act_input,
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=retry_for("standard"),
                    ),
                    workflow.execute_activity(
                        activities.run_frontend_mapping, act_input,
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=retry_for("standard"),
                    ),
                )

                # Route chain building (depends on framework + frontend)
                await workflow.execute_activity(
                    activities.run_route_chain_building, act_input,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=retry_for("standard"),
                )
                await workflow.execute_activity(
                    activities.log_phase_complete_activity,
                    ActivityInput(**{**act_input.__dict__, "phase": "pre-recon"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )

            if AgentName.RECON.value not in self._state.completed_agents:
                await workflow.execute_activity(
                    activities.log_phase_start_activity,
                    args=[
                        ActivityInput(**{**act_input.__dict__, "phase": "recon"}),
                        list(step_names("recon")),
                        list(step_intents("recon")),
                    ],
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
                self._state.current_phase = "recon"
                self._state.current_agent = AgentName.RECON.value
                metrics = await workflow.execute_activity(
                    activities.run_agent,
                    ActivityInput(**{**act_input.__dict__, "agent_name": AgentName.RECON.value}),
                    start_to_close_timeout=timedelta(hours=2),
                    retry_policy=retry_for("standard"),
                )
                self._state.completed_agents.append(AgentName.RECON.value)
                self._state.agent_metrics[AgentName.RECON.value] = metrics
                self._state.current_agent = None
                await workflow.execute_activity(
                    activities.log_phase_complete_activity,
                    ActivityInput(**{**act_input.__dict__, "phase": "recon"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )

            # Risk scoring — produce tiered audit plan
            await workflow.execute_activity(
                activities.log_phase_start_activity,
                args=[
                    ActivityInput(**{**act_input.__dict__, "phase": "risk-scoring"}),
                    list(step_names("risk-scoring")),
                    list(step_intents("risk-scoring")),
                ],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )
            self._state.current_phase = "risk-scoring"
            risk_result = await workflow.execute_activity(
                activities.run_risk_scoring, act_input,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_for("standard"),
            )
            self._state.audit_plan_stats = risk_result

            await workflow.execute_activity(
                activities.log_phase_complete_activity,
                ActivityInput(**{**act_input.__dict__, "phase": "risk-scoring"}),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )

            await workflow.execute_activity(
                activities.log_phase_start_activity,
                args=[
                    ActivityInput(**{**act_input.__dict__, "phase": "vulnerability-analysis"}),
                    list(vuln_phase_steps([str(vt) for vt in selected_classes])),
                    # 必须补齐第 3 参数(intents)，使 args 数量(3)== log_phase_start_activity
                    # 参数数(3)。否则 temporalio worker 检测到数量不匹配会把整个 arg_types
                    # 置 None，input 被反序列化成 dict 而非 ActivityInput → input.phase 崩。
                    # vuln steps 是动态 agent(vuln_classes 决定)，无静态 step_intents，故按
                    # 每个 vuln class 现造平行 intent 文案。
                    [f"分析 {vt} 漏洞" for vt in selected_classes],
                ],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )
            self._state.current_phase = "vulnerability-analysis"
            # Deterministic auth-config scan (spec §5.8 GitNexus track for vuln-auth).
            # Runs BEFORE the vuln agents so auth_config_scan.json is ready for
            # the vuln-auth LLM to read. Pure additive: zero findings -> empty
            # files, merger degrades to llm-only. Only runs when auth is in scope.
            if "auth" in [str(vt) for vt in selected_classes]:
                await workflow.execute_activity(
                    activities.run_auth_config_scan, act_input,
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=retry_for("standard"),
                )
            if input.enable_llm_track:
                vuln_tasks: list[tuple[VulnType, AgentName, object]] = []
                for vt in selected_classes:
                    agent_name = AgentName(f"{vt}-vuln")
                    if agent_name.value not in self._state.completed_agents:
                        self._state.current_agent = agent_name.value
                        coro = workflow.execute_activity(
                            activities.run_vuln_agent,
                            ActivityInput(**{**act_input.__dict__, "agent_name": agent_name.value}),
                            start_to_close_timeout=timedelta(hours=2),
                            retry_policy=retry_for("vuln"),
                        )
                        vuln_tasks.append((vt, agent_name, coro))
            else:
                # LLM 轨关闭: 只跑 GitNexus 轨, merge 只消费 *_gitnexus_queue.json
                workflow.logger.info("llm_track=disabled (SHANNON_LLM_TRACK_ENABLED=0); "
                                     "running GitNexus track only")
                vuln_tasks = []

            if vuln_tasks:
                semaphore = asyncio.Semaphore(input.max_concurrent)

                async def bounded(coro):
                    async with semaphore:
                        return await coro

                results = await asyncio.gather(
                    *[bounded(coro) for _, _, coro in vuln_tasks],
                    return_exceptions=True,
                )
                for (vt, agent_name, _), result in zip(vuln_tasks, results):
                    if isinstance(result, Exception):
                        self._state.errors.append(f"{agent_name.value}: {result}")
                        self._state.failed_agents.append(agent_name.value)
                    else:
                        self._state.completed_agents.append(agent_name.value)
                        self._state.agent_metrics[agent_name.value] = result
            # === Authz GitNexus track: judge IDOR candidates (spec §5.7) ===
            # Runs after vuln agents (LLM track queues ready) and before the
            # dual-track merge (Plan 3) so authz_gitnexus_queue.json exists
            # when merge reads it. Graceful: empty candidates → empty queue.
            try:
                await workflow.execute_activity(
                    activities.run_authz_gitnexus_judge, act_input,
                    start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=retry_for("standard"),
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "Authz GitNexus judge failed (non-fatal, LLM-only track continues): %s", exc)
            # === GitNexus-track chain verdict: inj/xss/ssrf (spec §5.4-5.6) ===
            # Produces <vuln>_gitnexus_queue.json for the dual-track merger.
            # Runs before run_merge_dual_track_queues (which reads those queues).
            # Non-fatal: failure degrades to LLM-only (merger tolerates absent
            # gitnexus queues). No parameter_graph.json (Plan 1 not landed) ->
            # empty, degrades to LLM-only (current behavior).
            try:
                await workflow.execute_activity(
                    activities.run_gitnexus_chain_verdict, act_input,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=retry_for("standard"),
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "GitNexus chain verdict failed (non-fatal, LLM-only track continues): %s", exc)
            await workflow.execute_activity(
                activities.run_merge_dual_track_queues,
                act_input,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_for("standard"),
            )
            await workflow.execute_activity(
                activities.log_phase_complete_activity,
                ActivityInput(**{**act_input.__dict__, "phase": "vulnerability-analysis"}),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )

            # === Attack Chain Assembly ===
            await workflow.execute_activity(
                activities.log_phase_start_activity,
                args=[
                    ActivityInput(**{**act_input.__dict__, "phase": "attack-chain"}),
                    list(step_names("attack-chain")),
                    list(step_intents("attack-chain")),
                ],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )
            self._state.current_phase = "attack-chain"
            try:
                await workflow.execute_activity(
                    activities.run_attack_chain_assembly, act_input,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=retry_for("standard"),
                )
            except Exception as exc:
                # Non-fatal — attack chains enhance the report but don't block the pipeline
                import logging
                logging.getLogger(__name__).warning("Attack chain assembly failed: %s", exc)
            await workflow.execute_activity(
                activities.log_phase_complete_activity,
                ActivityInput(**{**act_input.__dict__, "phase": "attack-chain"}),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )

            await workflow.execute_activity(
                activities.log_phase_start_activity,
                args=[
                    ActivityInput(**{**act_input.__dict__, "phase": "reporting"}),
                    list(step_names("reporting")),
                    list(step_intents("reporting")),
                ],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )
            self._state.current_phase = "reporting"
            self._state.current_agent = "render-findings"
            await workflow.execute_activity(
                activities.render_findings, act_input,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_for("standard"),
            )
            # 轴1:拼接各分项 → 综合报告(确定性)
            self._state.current_agent = "assemble-report"
            await workflow.execute_activity(
                activities.assemble_report, act_input,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_for("standard"),
            )
            # 轴1:REPORT agent 加执行摘要 + 清理(report-executive.txt)
            self._state.current_agent = "run-report-agent"
            await workflow.execute_activity(
                activities.run_agent,
                ActivityInput(**{**act_input.__dict__, "agent_name": "report"}),
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=retry_for("standard"),
            )
            self._state.current_agent = None
            await workflow.execute_activity(
                activities.log_phase_complete_activity,
                ActivityInput(**{**act_input.__dict__, "phase": "reporting"}),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )

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
