import asyncio
from datetime import timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.exceptions import ApplicationError as ApplicationFailure, CancelledError
from temporalio.common import RetryPolicy

from shannon_core.models.agents import (
    ALL_VULN_CLASSES,
    DEGRADABLE_VULN_CLASSES,
    AgentName,
    VulnType,
)
from shannon_core.models.errors import ErrorCode, PentestError
from shannon_core.config.vuln_selection import select_vuln_classes
# is_llm_track_enabled() 在 CLI 入口(main.py:77)读 env 一次注入 input.enable_llm_track,
# workflow 用 input.enable_llm_track 判定(守 Temporal 确定性: workflow code 不直接读 env).

from .shared import ActivityInput, PipelineInput, PipelineState, PipelineProgress
from .step_intents import step_names, step_intents


# run_code_index activity timeout: 文件级聚合后 sink+source+taint 三阶段累加仍偏紧,
# 10min 容不下大仓(真机 kol_mapping_service 撞 timeout)→ 提至 45min(spec 2026-07-10 §3.2;
# 2026-07-17 Koa+Sequelize 治本:sink/source/taint 串行最坏 30+min,45min 留余量)。
CODE_INDEX_ACTIVITY_TIMEOUT = timedelta(minutes=45)


def vuln_phase_steps(vuln_classes: list[str]) -> tuple[str, ...]:
    return tuple(f"{vt}-vuln" for vt in vuln_classes)


def _decide_gitnexus_failfast(statuses: dict, llm_track_enabled: bool) -> list[str]:
    """Task 4 fail-fast 决策(纯函数, 单测可达, 无 Temporal 依赖).

    返回需终止的 DEGRADABLE(inj/xss/ssrf)类列表(-> workflow raise ApplicationFailure).
    空 list = 继续. 三场景:
      - 关轨 + DEGRADABLE 任一 failed -> 返回该类(无 LLM 兜底, 终止).
      - 关轨 + 仅 authz failed -> [] 不终止(authz-vuln LLM 关轨仍跑, 做 GitNexus
        做不了的 Vertical/Context; authz 不在 DEGRADABLE_VULN_CLASSES).
      - 开轨 + 任何 fail -> [] 继续(LLM 轨兜底, merger/report 读状态产物标红).

    抽成纯函数: workflow 内联逻辑难以 Temporal 真机测(需 mock 整条链),
    抽出来后单测覆盖三场景 + 边界(状态缺失/多 fail), workflow 只调它 + raise.
    """
    if llm_track_enabled:
        return []
    return [
        vc for vc in DEGRADABLE_VULN_CLASSES
        if statuses.get(vc, {}).get("status") == "failed"
    ]


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

        # C1 Phase B: worker 容器路径门控. event_file 非 None = web 提交(worker 路径),
        # 调迁移 activity(setup_display/heartbeat/finalize); None = CLI 路径, run_scan 外层
        # 已 set_audit_session/heartbeat/log_workflow_complete, workflow 内不重复(守 "CLI
        # 零改动" + 消除 R1 双重 scan_end/heartbeat/set_audit_session).
        is_worker_path = input.event_file is not None
        heartbeat_handle = None

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

        # Compute workspace_path so activities know where to write 产物(heartbeat/deliverables/
        # activity_failures/agents/workflow.log/session). WEB 路径(event_file 非 None): 用 event_file
        # 同目录(web scan_manager 创建的 /app/workspaces/<ws>), 与 web 判活(is_scan_alive 看
        # <ws>/heartbeat)/报告读取(get_workspace_vuln_counts)对齐。CLI 路径(无 event_file): 走
        # repo_path.parent/workspaces(CLI 习惯, run_scan 外层建)。修路径分歧(2026-07-14 端到端暴露:
        # worker 产物原落 /app/repos/.../workspaces/<ws>, web 找不到 heartbeat → 判活失效 + 报告空).
        if input.event_file:
            workspace_path = str(Path(input.event_file).parent)
        elif input.workspace_name:
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
            event_file=input.event_file,
        )
        # C1 Phase B: worker 路径前导 setup_display(注入 AuditSession + event_file) + 并行
        # run_heartbeat(长驻写 heartbeat). CLI 路径跳过(外层 run_scan 已做).
        if is_worker_path:
            await workflow.execute_activity(
                activities.setup_display, act_input,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_for("standard"),
            )
            # run_heartbeat 是并行 long-running activity(永阻塞, 靠 cancel 退出). 必须
            # asyncio.create_task 包起才会实际调度执行——裸 workflow.execute_activity(...) 仅
            # 产 coroutine, 不 await/create_task 则 activity 永不执行 → heartbeat 永不写 →
            # web 判活在 120s 提交宽限后误判 interrupted(2026-07-14 端到端验证暴露). 返回 Task
            # 供下方 finally 的 heartbeat_handle.cancel() 终止(Task.cancel 有效; coroutine 无).
            heartbeat_handle = asyncio.create_task(workflow.execute_activity(
                activities.run_heartbeat, act_input,
                start_to_close_timeout=timedelta(hours=24),
                retry_policy=RetryPolicy(maximum_attempts=1),
            ))
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

                # pre-recon LLM 始终跑(不再受 enable_llm_track 门控): recon 链是 authz
                # Vertical/Context 的输入(角色模型 §7 / 工作流 §8.3), GitNexus 完全不产 ——
                # 关 LLM 轨也必须跑, 否则 authz 巧妇难为无米之炊(plan smooth-wandering-dolphin)。
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

                self._state.code_index_stats = code_index_result

                # merge_sink_reports 始终跑(不再门控): 依赖 pre-recon deliverable,
                # 保 pre-recon(上)则保它。合并确定性 sinks + LLM-discovered sinks。
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
                # recon LLM 始终跑(不再门控): authz Vertical/Context 的输入
                # (角色模型 §7 / 工作流 §8.3 / 端点安全上下文 §4.2), GitNexus 完全不产。
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

            # phase start 的 step/intent 列表对齐实际调度的 vuln agent: 关 LLM 轨时只
            # authz/auth-vuln 跑(inj/xss/ssrf 靠 GitNexus chain_verdict, 在本 phase 后段,
            # 非 vuln agent, 不在此 step 列表), 避免显示 inj-vuln 等不跑的 agent 误导。
            if input.enable_llm_track:
                vuln_display = list(selected_classes)
            else:
                vuln_display = [vt for vt in selected_classes
                                if vt not in DEGRADABLE_VULN_CLASSES]
            await workflow.execute_activity(
                activities.log_phase_start_activity,
                args=[
                    ActivityInput(**{**act_input.__dict__, "phase": "vulnerability-analysis"}),
                    list(vuln_phase_steps([str(vt) for vt in vuln_display])),
                    # 必须补齐第 3 参数(intents)，使 args 数量(3)== log_phase_start_activity
                    # 参数数(3)。否则 temporalio worker 检测到数量不匹配会把整个 arg_types
                    # 置 None，input 被反序列化成 dict 而非 ActivityInput → input.phase 崩。
                    # vuln steps 是动态 agent(vuln_classes 决定)，无静态 step_intents，故按
                    # 每个 vuln class 现造平行 intent 文案。
                    [f"分析 {vt} 漏洞" for vt in vuln_display],
                ],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )
            self._state.current_phase = "vulnerability-analysis"
            vuln_tasks: list[tuple[VulnType, AgentName, object]] = []
            if input.enable_llm_track:
                # 开轨: 全部 selected_classes 跑(双轨: LLM vuln agent + GitNexus 轨 OR)
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
                # 关 LLM 轨: 只关 inj/xss/ssrf(taint, GitNexus chain_verdict 主干兜底);
                # authz/auth 必须保留(GitNexus 只做 IDOR 不覆盖 Vertical/Context, auth 无确定性轨)。
                # recon/pre-recon LLM 已在上游始终跑(本 gate 之外), 保证 authz 有输入不降级。
                for vt in selected_classes:
                    if vt in DEGRADABLE_VULN_CLASSES:
                        continue
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
                await workflow.execute_activity(
                    activities.log_info_activity,
                    ActivityInput(**{**act_input.__dict__,
                       "info_message": "llm_track=disabled (SHANNON_LLM_TRACK_ENABLED=0): inj/xss/ssrf vuln agents skipped (GitNexus chain_verdict fallback); authz/auth vuln agents + recon/pre-recon LLM retained",
                       "info_level": "info"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )

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
            # when merge reads it. Graceful: empty candidates -> empty queue.
            # Task 4 fail-fast: 删 try/except 吞异常; activity 返 failed=True 经
            # 状态产物标红, 不再走"LLM 兜底"降级(authz-vuln LLM 轨在关轨时仍跑,
            # 由 _decide_gitnexus_failfast 判定, authz fail 永不终止).
            _authz_gn = await workflow.execute_activity(
                activities.run_authz_gitnexus_judge, act_input,
                start_to_close_timeout=timedelta(minutes=30),  # 原 10；多轮 agent 窗口（spec-0）
                retry_policy=retry_for("gitnexus-verdict"),  # 原 standard；spec-1a 切（多轮 agent，max 3）
            )
            # === GitNexus-track chain verdict: inj/xss/ssrf (spec §5.4-5.6) ===
            # Produces <vuln>_gitnexus_queue.json for the dual-track merger.
            # Task 4 fail-fast: 删 try/except; activity 返 failed_classes/fail_reasons,
            # workflow 据返回值判 fail-fast(关轨 + DEGRADABLE fail -> 终止).
            _gn_verdict = await workflow.execute_activity(
                activities.run_gitnexus_chain_verdict, act_input,
                start_to_close_timeout=timedelta(minutes=15),  # 原 5；多轮 agent 窗口（spec-0）
                retry_policy=retry_for("standard"),
            )

            # === fail-fast 编排:汇总两轨状态,写 gitnexus_track_status.json ===
            # 状态产物供 merger/report 读(开轨标红); 关轨 + DEGRADABLE fail 由
            # _decide_gitnexus_failfast 判定后 raise 终止(无 LLM 兜底).
            _statuses: dict = {}
            for _vc, _n in (_gn_verdict or {}).get("per_class", {}).items():
                _statuses[_vc] = {"status": "ok", "findings": _n}
            for _vc in (_gn_verdict or {}).get("failed_classes", []):
                _statuses[_vc] = {"status": "failed",
                                  "reason": (_gn_verdict or {}).get("fail_reasons", {}).get(_vc, "unknown")}
            if _authz_gn is not None:
                if _authz_gn.get("failed"):
                    _statuses["authz"] = {"status": "failed", "reason": _authz_gn.get("fail_reason", "unknown")}
                else:
                    _statuses["authz"] = {"status": "ok", "findings": _authz_gn.get("verdict_count", 0)}
            await workflow.execute_activity(
                activities.write_track_status_activity,
                ActivityInput(**{**act_input.__dict__, "track_statuses": _statuses}),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )

            # 关轨终止:仅 DEGRADABLE(inj/xss/ssrf)的 GitNexus fail 是真·无 LLM 兜底 -> 终止。
            # authz GitNexus fail(任何模式)+ 开轨的 inj/xss/ssrf fail -> 标红继续(merger/report 读状态产物)
            # 关轨判定用 input.enable_llm_track(已在 workflow 入口由 CLI 从 is_llm_track_enabled()
            # 读 env 一次注入), 守 Temporal 确定性(workflow code 不应直接读 env).
            if _no_fallback_failed := _decide_gitnexus_failfast(
                _statuses, llm_track_enabled=input.enable_llm_track
            ):
                raise ApplicationFailure(
                    f"GitNexus 轨 fail-fast(关轨模式):{_no_fallback_failed} 判定失败,"
                    f"且这些类关轨后无 LLM 轨兜底 -> 终止扫描。",
                    type="GitNexusTrackFailure", non_retryable=True,
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
            # 攻击链章节最后注入（report-executive 之后），避免被 agent 重写覆盖丢失
            self._state.current_agent = "inject-attack-chains"
            await workflow.execute_activity(
                activities.inject_attack_chains, act_input,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_for("standard"),
            )
            # GitNexus 轨判定状态注记(report-executive 之后,防覆盖;fail-fast plan Task 6)
            self._state.current_agent = "inject-gitnexus-track-status"
            await workflow.execute_activity(
                activities.inject_gitnexus_track_status, act_input,
                start_to_close_timeout=timedelta(minutes=2),
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
            # C1 Phase B: worker 路径后置 finalize_summary(写 scan_end + 清 AuditSession) +
            # 停 heartbeat. CLI 路径跳过(外层 run_scan 已 log_workflow_complete).
            if is_worker_path:
                if heartbeat_handle is not None:
                    try:
                        heartbeat_handle.cancel()
                    except Exception:
                        pass
                from shannon_core.models.audit import AgentMetricsSummary
                summary = {
                    "status": self._state.status,
                    "total_duration_ms": int((workflow.time_ns() / 1e9 - self._state.start_time) * 1000),
                    "total_cost_usd": sum((m.get("cost_usd") or 0.0) for m in self._state.agent_metrics.values()),
                    "completed_agents": list(self._state.completed_agents),
                    "agent_metrics": {
                        name: AgentMetricsSummary(
                            duration_ms=int(m.get("duration_ms", 0) or 0),
                            cost_usd=m.get("cost_usd"),
                        )
                        for name, m in self._state.agent_metrics.items()
                    },
                    "error": (self._state.errors[0] if self._state.errors else None),
                }
                await workflow.execute_activity(
                    activities.finalize_summary, args=[act_input, summary],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=retry_for("standard"),
                )
            self._state.current_phase = None
            return self._state
        except CancelledError:
            self._state.status = "cancelled"
            if heartbeat_handle is not None:
                try:
                    heartbeat_handle.cancel()
                except Exception:
                    pass
            self._state.current_phase = None
            return self._state
        finally:
            if heartbeat_handle is not None:
                try:
                    heartbeat_handle.cancel()
                except Exception:
                    pass
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
