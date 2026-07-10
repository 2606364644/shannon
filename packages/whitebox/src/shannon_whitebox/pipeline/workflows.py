import asyncio
from datetime import timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.exceptions import CancelledError

from shannon_core.models.agents import AgentName, ALL_VULN_CLASSES, VulnType
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.config.vuln_selection import select_vuln_classes

from .shared import ActivityInput, PipelineInput, PipelineState, PipelineProgress
from .step_intents import step_names, step_intents


# run_code_index activity timeout: 文件级聚合后 sink+source+taint 三阶段累加仍偏紧,
# 10min 容不下大仓(真机 kol_mapping_service 撞 timeout)→ 提至 20min(spec 2026-07-10 §3.2)。
CODE_INDEX_ACTIVITY_TIMEOUT = timedelta(minutes=20)


def vuln_phase_steps(vuln_classes: list[str]) -> tuple[str, ...]:
    return tuple(f"{vt}-vuln" for vt in vuln_classes)


with workflow.unsafe.imports_passed_through():
    from . import activities
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
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

        # Resolve config (YAML) early so vuln-class selection can consult cfg.vuln_classes.
        cfg = None
        if input.config_path:
            from shannon_core.config.parser import parse_config
            cfg = parse_config(input.config_path)

        # vuln 类优先级链: CLI/env override(经 input.vuln_classes) > YAML(cfg.vuln_classes) > 默认全跑。
        # 修通 pre-existing 断链（旧: input.vuln_classes or ALL_VULN_CLASSES，丢弃 cfg.vuln_classes）。
        selected_classes: list[VulnType] = select_vuln_classes(
            input.vuln_classes,
            cfg.vuln_classes if cfg else None,
        )

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

        await workflow.execute_activity(
            activities.log_phase_complete_activity,
            ActivityInput(**{**act_input.__dict__, "phase": "setup"}),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=retry_for("log"),
        )

        # Write code path deny rules (S6)
        if cfg and cfg.rules and cfg.rules.avoid:
            sync_code_path_deny_rules(cfg.rules.avoid)

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

                if input.enable_llm_track:
                    # Fail-fast: if either fails, cancel the other and propagate.
                    code_index_result, pre_recon_metrics = await asyncio.gather(
                        workflow.execute_activity(
                            activities.run_code_index, act_input,
                            start_to_close_timeout=CODE_INDEX_ACTIVITY_TIMEOUT,
                            retry_policy=retry_for("code-index"),
                        ),
                        workflow.execute_activity(
                            activities.run_agent,
                            ActivityInput(**{**act_input.__dict__, "agent_name": AgentName.PRE_RECON.value}),
                            start_to_close_timeout=timedelta(hours=2),
                            retry_policy=retry_for("standard"),
                        ),
                    )
                    self._state.completed_agents.append(AgentName.PRE_RECON.value)
                    self._state.agent_metrics[AgentName.PRE_RECON.value] = pre_recon_metrics
                else:
                    # LLM 轨关闭: pre-recon LLM agent 跳过, 只跑 code_index (GitNexus 确定性兜底).
                    # 不 append PRE_RECON (resume 语义: 开轨重跑会补); entry_point_fusion 内部
                    # 靠 deliverable 存在性 skip LLM 源 (G6 解耦), 故 pre_recon_deliverable.md 缺失安全.
                    code_index_result = await workflow.execute_activity(
                        activities.run_code_index, act_input,
                        start_to_close_timeout=CODE_INDEX_ACTIVITY_TIMEOUT,
                        retry_policy=retry_for("code-index"),
                    )
                    await workflow.execute_activity(
                        activities.log_info_activity,
                        ActivityInput(**{**act_input.__dict__,
                           "info_message": "llm_track=disabled (SHANNON_LLM_TRACK_ENABLED=0): pre-recon LLM agent skipped; code_index (GitNexus) still runs; entry points degrade to deterministic schema source only",
                           "info_level": "info"}),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=retry_for("log"),
                    )

                self._state.code_index_stats = code_index_result

                if input.enable_llm_track:
                    # Merge deterministic sinks with LLM-discovered sinks (needs LLM deliverable)
                    await workflow.execute_activity(
                        activities.run_merge_sink_reports, act_input,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=retry_for("standard"),
                    )

                # Entry point fusion: schema/convention 无条件跑（纯确定性 + LLM 源靠
                # deliverable 存在性内部 skip）；G6 解耦——不再被 enable_llm_track 门控，
                # 让关 LLM 轨时 GitNexus 轨仍融合 OpenAPI schema 源（兜底不丢入口）。
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
                if input.enable_llm_track:
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
                else:
                    # LLM 轨关闭: recon LLM agent 跳过. 不进 recon phase, 不 append RECON
                    # (resume 语义). GitNexus 轨不依赖 recon_deliverable.md (chain_verdict
                    # 只吃 parameter_graph.json), 故缺失安全; 下游 vuln/attack_chain/PoC 靠
                    # exists() 守卫降级 (spec §1.3 零硬依赖崩).
                    await workflow.execute_activity(
                        activities.log_info_activity,
                        ActivityInput(**{**act_input.__dict__,
                           "info_message": "llm_track=disabled (SHANNON_LLM_TRACK_ENABLED=0): recon LLM agent skipped; GitNexus track continues independently",
                           "info_level": "info"}),
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
                try:
                    _auth_scan = await workflow.execute_activity(
                        activities.run_auth_config_scan, act_input,
                        start_to_close_timeout=timedelta(minutes=3),
                        retry_policy=retry_for("standard"),
                    )
                    await workflow.execute_activity(
                        activities.log_info_activity,
                        ActivityInput(**{**act_input.__dict__,
                           "info_message": f"Auth config scan ok: {_auth_scan.get('total_findings', 0)} findings",
                           "info_level": "info"}),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=retry_for("log"),
                    )
                except Exception as exc:
                    await workflow.execute_activity(
                        activities.log_info_activity,
                        ActivityInput(**{**act_input.__dict__,
                           "info_message": f"Auth config scan failed (non-fatal, auth track degrades to LLM-only): {exc}",
                           "info_level": "warning"}),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=retry_for("log"),
                    )
                # === Auth GitNexus-track logic-class judge (spec-2b T6) ===
                # config_scan 先产 auth_gitnexus_queue.json 的 config 类条目
                # (cookie/HSTS/CORS/JWT/限流)；auth_judge 追加逻辑类 verdict
                # (session 固定/明文密码/OAuth state 缺失/弱随机 token)，读现有 +
                # 合并非覆盖。Graceful：失败时 queue 保留 config_scan 产出，不阻塞
                # LLM 轨。多轮 agent 窗口（候选>0 深判 / 候选=0 自主探索）。
                try:
                    await workflow.execute_activity(
                        activities.run_auth_gitnexus_judge, act_input,
                        start_to_close_timeout=timedelta(minutes=30),
                        retry_policy=retry_for("gitnexus-verdict"),
                    )
                except Exception as exc:
                    await workflow.execute_activity(
                        activities.log_info_activity,
                        ActivityInput(**{**act_input.__dict__,
                           "info_message": f"Auth GitNexus judge failed (non-fatal, config-scan queue preserved, LLM-only track continues): {exc}",
                           "info_level": "warning"}),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=retry_for("log"),
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
                await workflow.execute_activity(
                    activities.log_info_activity,
                    ActivityInput(**{**act_input.__dict__,
                       "info_message": "llm_track=disabled (SHANNON_LLM_TRACK_ENABLED=0); running GitNexus track only",
                       "info_level": "info"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
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
                    start_to_close_timeout=timedelta(minutes=30),  # 原 10；多轮 agent 窗口（spec-0）
                    retry_policy=retry_for("gitnexus-verdict"),  # 原 standard；spec-1a 切（多轮 agent，max 3）
                )
            except Exception as exc:
                await workflow.execute_activity(
                    activities.log_info_activity,
                    ActivityInput(**{**act_input.__dict__,
                       "info_message": f"Authz GitNexus judge failed (non-fatal, LLM-only track continues): {exc}",
                       "info_level": "warning"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
            # === GitNexus-track chain verdict: inj/xss/ssrf (spec §5.4-5.6) ===
            # Produces <vuln>_gitnexus_queue.json for the dual-track merger.
            # Runs before run_merge_dual_track_queues (which reads those queues).
            # Non-fatal: failure degrades to LLM-only (merger tolerates absent
            # gitnexus queues). No parameter_graph.json (empty taint graph) ->
            # empty, degrades to LLM-only.
            try:
                _gn_verdict = await workflow.execute_activity(
                    activities.run_gitnexus_chain_verdict, act_input,
                    start_to_close_timeout=timedelta(minutes=15),  # 原 5；多轮 agent 窗口（spec-0）
                    retry_policy=retry_for("standard"),
                )
                await workflow.execute_activity(
                    activities.log_info_activity,
                    ActivityInput(**{**act_input.__dict__,
                       "info_message": f"GitNexus chain verdict ok: {_gn_verdict.get('per_class', {})}",
                       "info_level": "info"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
            except Exception as exc:
                await workflow.execute_activity(
                    activities.log_info_activity,
                    ActivityInput(**{**act_input.__dict__,
                       "info_message": f"GitNexus chain verdict failed (non-fatal, LLM-only track continues): {exc}",
                       "info_level": "warning"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
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

            # === Attack Chain (dual-track, post-vuln) ===
            # LLM track (run_attack_chain_llm_agent) 跑 attack-chain agent
            # (Write attack_chains_llm_queue.json)；GitNexus track
            # (run_attack_chain_assembly_v2) 组装 GitNexus chains + 合并两轨
            # → attack_chains.json。两步均非 fatal（attack chains 增强报告不阻塞）。
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
                    activities.run_attack_chain_llm_agent, act_input,
                    start_to_close_timeout=timedelta(minutes=30),
                    retry_policy=retry_for("standard"),
                )
            except Exception as exc:
                # Non-fatal — LLM track 失败时 GitNexus 轨仍可独立产 chains
                await workflow.execute_activity(
                    activities.log_info_activity,
                    ActivityInput(**{**act_input.__dict__,
                       "info_message": f"Attack chain LLM agent failed (non-fatal, GitNexus-only track continues): {exc}",
                       "info_level": "warning"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
            try:
                await workflow.execute_activity(
                    activities.run_attack_chain_assembly_v2, act_input,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=retry_for("standard"),
                )
            except Exception as exc:
                # Non-fatal — attack chains 增强报告但不阻塞主流程
                await workflow.execute_activity(
                    activities.log_info_activity,
                    ActivityInput(**{**act_input.__dict__,
                       "info_message": f"Attack chain assembly v2 failed (non-fatal): {exc}",
                       "info_level": "warning"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
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
                activities.assemble_report,
                ActivityInput(**{**act_input.__dict__,
                                 "vuln_classes": [str(vt) for vt in selected_classes]}),
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
            # === 报告增强：生成 PoC md（失败由 activity 内部吞掉，不影响主报告） ===
            self._state.current_agent = "generate-poc-report"
            await workflow.execute_activity(
                activities.generate_poc_report, act_input,
                start_to_close_timeout=timedelta(minutes=20),
                retry_policy=retry_for("poc"),
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
