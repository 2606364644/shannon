import asyncio
import logging
from datetime import timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError, CancelledError

from supernova_core.models.agents import AgentName, ALL_VULN_CLASSES
from supernova_core.agents.progress_tool import AUTH_VALIDATION_PROGRESS
from supernova_core.utils.paths import (
    resolve_deliverables_path,
    has_valid_whitebox_results,
    resolve_track_deliverable,
    WHITEBOX_SUBDIR,
)

from .shared import BlackboxActivityInput, BlackboxAuthValidationInput, BlackboxPipelineInput, BlackboxPipelineState, PipelineProgress

logger = logging.getLogger(__name__)


def has_correlation_results(corr_ws_deliverables: Path, vuln_classes: list[str]) -> bool:
    """§6.2 闭环检测端:关联 workspace 的 merged queue 是否构成黑盒 recon-skip 复用源。

    纯函数(不依赖 Temporal,可单测)。当 `--correlated-workspace` 指定的关联
    workspace deliverables 里存在 `vuln_classes` 中至少一个漏洞类的有效
    `{vt}_exploitation_queue.json` 时返回 True。有效性复用
    `has_valid_whitebox_results`(每条 entry 必须含 title/description/severity/location
    四字段,spec §7 B1 硬约束;跨服务额外字段如 service/cross_service_source 因 subset
    检查不破坏判定)。

    与单仓 deliverables 检查的关系:ADD 源(任一有效即 skip recon),非 replace。
    调用方(workflow `run`)仅在 `input.correlated_workspace` 设置时调用本函数——
    `correlated_workspace` 为 None(所有单仓 / 现有 `--repo`·`--latest` 调用)时
    根本不进入此路径,行为与改动前字节一致(单仓零回归)。
    """
    if not vuln_classes:
        return False
    for vt in vuln_classes:
        queue_file = resolve_track_deliverable(
            corr_ws_deliverables, WHITEBOX_SUBDIR, f"{vt}_exploitation_queue.json")
        if has_valid_whitebox_results(queue_file):
            return True
    return False


with workflow.unsafe.imports_passed_through():
    from . import activities
    from supernova_core.utils.progress import (
        AgentOutcome,
        exploit_result_to_outcome,
        format_exploit_summary,
    )
    from supernova_core.services.settings_writer import cleanup_settings
    from supernova_core.services.playwright_config_writer import (
        get_session_id,
    )
    from supernova_core.services.validate_authentication import AuthValidationResult
    from supernova_core.models.retry import retry_for
    from supernova_core.models.errors import PentestError, ErrorCode, classify_error_for_temporal


@workflow.defn
class BlackboxScanWorkflow:
    def __init__(self):
        self._state = BlackboxPipelineState()

    @workflow.run
    async def run(self, input: BlackboxPipelineInput) -> BlackboxPipelineState:
        self._state.start_time = workflow.time_ns() / 1e9

        # workspaces 根由 sandbox 外（CLI/worker）解析后经 input 传入；sandbox 内禁
        # os.getenv/Path.cwd（否则 RestrictedWorkflowAccessError）。run 体内只用此值，零 I/O。
        if not input.workspaces_root:
            raise ValueError(
                "BlackboxPipelineInput.workspaces_root must be set before starting the "
                "workflow (sandbox cannot resolve it)."
            )
        ws_root = Path(input.workspaces_root)

        selected_classes: list[str] = input.vuln_classes or list(ALL_VULN_CLASSES)

        # C1 Phase B（黑盒 web 化）：event_file 非 None = web 提交（worker 路径），调迁移
        # activity（setup_display/finalize_summary）；None = CLI 路径，run_scan 外层已
        # set_audit_session/heartbeat/log_workflow_complete，workflow 内不重复（守 CLI 零改动）。
        # 对齐 whitebox workflows.py:97-101 is_worker_path 门控。
        is_worker_path = input.event_file is not None

        # Compute workspace_path so activities know where to write 产物（heartbeat/deliverables/
        # workflow.log/session）。WEB 路径（event_file 非 None）：用 event_file 同目录（= scan_dir，
        # web scan_manager 创建的 workspaces/<ws>/scans/<scan_id>/），与 web 判活（heartbeat）/
        # DeliverablesReader 读取对齐——修历史分裂（产物原落 ws_root/workspace_name 平铺，web 读不到）。
        # CLI 路径（无 event_file）：走 ws_root/workspace_name（与旧 resolve_deliverables_path 同路径）。
        if input.event_file:
            workspace_path = str(Path(input.event_file).parent)
        elif input.workspace_name:
            workspace_path = str(ws_root / input.workspace_name)
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
            correlated_workspace=input.correlated_workspace,
            event_file=input.event_file,
        )

        retry_policy = retry_for(
            "standard",
            "testing" if input.pipeline_testing_mode else (input.retry_profile or "production"),
        )

        # C1 Phase B：worker 路径前导 setup_display（注入 AuditSession + event_file + heartbeat），
        # 必须在首个 activity 前挂 StructuredEventRenderer，否则 preflight/auth 阶段事件不落盘。
        # CLI 路径跳过（外层 run_scan 已 set_audit_session/heartbeat）。对齐 whitebox:144-149。
        if is_worker_path:
            await workflow.execute_activity(
                activities.setup_display, act_input,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_for("standard"),
            )

        await workflow.execute_activity(
            activities.log_phase_start_activity,
            args=[
                BlackboxActivityInput(**{**act_input.__dict__, "phase": "preflight"}),
                [],
                [],
            ],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=retry_for("log"),
        )
        await workflow.execute_activity(
            activities.run_blackbox_preflight, act_input,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry_for("preflight"),
        )

        # Resolve config and browser engine — 文件 I/O 与副作用(parse_config 读 yaml /
        # check_available 走 shutil.which / sync_code_path_deny_rules 写 settings.json /
        # write_config 写 stealth config)经 resolve_blackbox_engine activity 完成,
        # sandbox 禁这些操作。返回 engine_name 供 exploit 循环复用。
        engine_name = await workflow.execute_activity(
            activities.resolve_blackbox_engine, act_input,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=retry_for("log"),
        )

        # Auth validation when config is present
        if input.config_path:
            await workflow.execute_activity(
                activities.log_phase_start_activity,
                args=[
                    BlackboxActivityInput(**{**act_input.__dict__, "phase": "auth-validation"}),
                    [],
                    [],
                ],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )
            await workflow.execute_activity(
                activities.run_blackbox_auth_validation, act_input,
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry_for("auth-validation"),
            )

        try:
            # C1 Phase B：deliverables 落点优先 workspace_path（web=scan_dir/deliverables_subdir），
            # 与 _get_deliverables_path（activities.py）+ web DeliverablesReader 读取口径对齐。
            # CLI 路径 workspace_path 上方已算（ws_root/workspace_name，与旧 resolve_deliverables_path
            # 同路径，零回归）；兜底分支防 standalone（无 repo 无 ws）workspace_path 为空。
            if workspace_path:
                deliverables = Path(workspace_path) / input.deliverables_subdir
            else:
                deliverables = resolve_deliverables_path(
                    repo_path=input.repo_path,
                    deliverables_subdir=input.deliverables_subdir,
                    workspace_name=input.workspace_name,
                    workspaces_root=ws_root,
                )

            # B2: 当指定关联 workspace 时，加载其 topology/boundaries 作为 exploitation 上下文。
            # ws_root 由 sandbox 外（CLI/worker）解析后经 input 传入（honors SUPERNOVA_WORKER_ROOT +
            # find_project_root() 口径）；文件读取经 activity 完成（sandbox 禁 Path.exists/read_text）。
            corr_ctx = None
            corr_ws_path = None
            if input.correlated_workspace:
                corr_ws_path = ws_root / input.correlated_workspace
                corr_ctx = await workflow.execute_activity(
                    activities.load_correlation_context,
                    str(corr_ws_path),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=retry_for("log"),
                )
            self._state.correlation_context = corr_ctx  # 供 exploitation 读取（B3）

            # 白盒结果检测（单仓 + §6.2 关联 workspace ADD 源）——文件 I/O（读 queue）经
            # detect_whitebox_results activity 完成（sandbox 禁 has_valid_whitebox_results/
            # has_correlation_results）。corr 路径由 sandbox 外拼好以 str 传入；ADD 语义
            # （单仓无结果才查关联）在 activity 内。state 更新与 log 留 workflow（用返回值驱动）。
            corr_dlv_path = (
                str(corr_ws_path / "deliverables")
                if (input.correlated_workspace and corr_ws_path is not None)
                else None
            )
            # 根因 1 修复：读白盒 queue 的根 = repo_path/deliverables_subdir（白盒 scan_dir/
            # deliverables，白盒产物所在），而非黑盒自己 deliverables（workspace_path/
            # deliverables_subdir，空）——否则 exploitation 5 类全 queue_file_not_found skip。
            # standalone（无 repo_path）回落黑盒自己 deliverables（detect 必返 False → 1.2 fail-fast）。
            wb_queue_root = (
                str(Path(input.repo_path) / input.deliverables_subdir)
                if input.repo_path else str(deliverables)
            )
            wb = await workflow.execute_activity(
                activities.detect_whitebox_results,
                args=[wb_queue_root, selected_classes, corr_dlv_path],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_for("log"),
            )
            has_whitebox_results: bool = wb["has_whitebox_results"]
            found_classes: list[str] = wb["found_classes"]
            # 对齐 TS validateDeliverablesExist：recon_deliverable.md 存在性（攻击面情报完整性）。
            # .get(..., True) 向后兼容旧 activity（不返回此字段时不阻塞）。
            has_recon_deliverable: bool = wb.get("has_recon_deliverable", True)
            self._state.has_whitebox_results = has_whitebox_results
            self._state.found_whitebox_classes = found_classes

            # §6.2 闭环：关联 workspace 贡献了结果时（单仓无结果、关联命中），合并 found_classes
            # 并记录日志（ADD 源可观测性）。corr_classes 非空 ⟺ activity 内单仓无结果且关联命中。
            corr_classes: list[str] = wb["corr_classes"]
            if corr_classes:
                found_classes = found_classes + [
                    vt for vt in corr_classes if vt not in found_classes
                ]
                self._state.has_whitebox_results = True
                self._state.found_whitebox_classes = found_classes
                await workflow.execute_activity(
                    activities.log_info_activity,
                    BlackboxActivityInput(**{**act_input.__dict__,
                       "info_message": f"Correlation workspace results detected at {corr_ws_path}/deliverables for classes: {corr_classes} — skipping RECON_BLACKBOX (§6.2 closed loop)",
                       "info_level": "info"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )

            # 对齐 TS validateDeliverablesExist（activities.ts:1330）：recon_deliverable.md 缺失即
            # nonRetryable fail（即使 queue 非空）。recon 是全局攻击面情报（API inventory / input
            # vectors / 技术栈），缺失则 exploit agent 失明。此前错误消息写了 recon 但未真校验。
            if not has_recon_deliverable:
                raise PentestError(
                    "Blackbox scan requires whitebox recon_deliverable.md (attack-surface "
                    f"intelligence) under {wb_queue_root}/{WHITEBOX_SUBDIR}/. It is missing — "
                    "exploit agents would run blind without API inventory / input vectors. "
                    "Re-run the whitebox scan (its recon phase produces recon_deliverable.md) "
                    "before reusing its results.",
                    "whitebox",
                    error_code=ErrorCode.DELIVERABLE_NOT_FOUND,
                )

            if has_whitebox_results:
                await workflow.execute_activity(
                    activities.log_info_activity,
                    BlackboxActivityInput(**{**act_input.__dict__,
                       "info_message": f"Whitebox results detected at {wb_queue_root} for classes: {found_classes} — skipping RECON_BLACKBOX",
                       "info_level": "info"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
            else:
                # 对齐 TS validateDeliverablesExist：黑盒 = 白盒下游 exploitation-only，不独立发现漏洞
                # （TS 黑盒本身无 recon/analysis 阶段，强制要求白盒产物，standalone hard fail）。
                # 无白盒产物 → fail-fast（recon 阶段已于阶段 2 删除，黑盒恒复用白盒）。PentestError
                # 被 workflow except 捕获 → state.status=failed + return；is_worker_path 时 _finalize_web 写 scan_end + failed。
                raise PentestError(
                    "Blackbox scan requires existing whitebox scan deliverables "
                    f"(recon_deliverable.md + non-empty *_exploitation_queue.json) under "
                    f"{wb_queue_root}/{WHITEBOX_SUBDIR}/. Run a whitebox scan first or reuse its "
                    "results. Blackbox is exploitation-only and does not run recon.",
                    "whitebox",
                    error_code=ErrorCode.DELIVERABLE_NOT_FOUND,
                )

            if input.exploit:
                await workflow.execute_activity(
                    activities.log_phase_start_activity,
                    args=[
                        BlackboxActivityInput(**{**act_input.__dict__, "phase": "exploitation"}),
                        [],
                        [],
                    ],
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
                self._state.current_phase = "exploitation"
                self._state.current_agent = "pipelines"
                # spec 2026-08-03: 端点 live 验证(exploitation 前)。读白盒端点 + auth-state,
                # 验证 live + 路由转发前缀探测,产 blackbox/endpoint_verify.json。功能性失败 →
                # 降级(activity 内部吞异常返 endpoint_verify=None,不 raise),exploit 全打(零回归)。
                # 无 web_url(黑盒无 live target)→ 跳过(exploit 照打)。maximum_attempts=1:增强
                # 功能不重试(失败=现状)。exploit 衔接(读 endpoint_verify.json)见 ExploitExecutor。
                if input.web_url:
                    await workflow.execute_activity(
                        activities.run_endpoint_verify, act_input,
                        start_to_close_timeout=timedelta(minutes=15),
                        retry_policy=RetryPolicy(maximum_attempts=1),
                    )
                # Queue gating: validate queue files before scheduling exploit agents
                validation_results = []
                exploit_tasks = []
                for vt in selected_classes:
                    exploit_check_input = BlackboxActivityInput(
                        **{**act_input.__dict__, "vuln_type": vt}
                    )
                    validation = await workflow.execute_activity(
                        activities.validate_exploitation_queue,
                        exploit_check_input,
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=retry_for("log"),
                    )
                    validation_results.append((vt, validation))
                    if not validation.valid:
                        if validation.is_expected:
                            logger.debug(
                                "Skipping exploit for %s (expected): %s",
                                vt, validation.message,
                            )
                        else:
                            await workflow.execute_activity(
                                activities.log_info_activity,
                                BlackboxActivityInput(**{**act_input.__dict__,
                                   "info_message": f"Skipping exploit for {vt} (anomalous): {validation.message} | queue_path={validation.context.get('queue_path', 'N/A')}",
                                   "info_level": "warning"}),
                                start_to_close_timeout=timedelta(seconds=10),
                                retry_policy=retry_for("log"),
                            )
                        continue
                    agent_name = AgentName(f"{vt}-exploit")
                    if agent_name.value not in self._state.completed_agents:
                        self._state.current_agent = agent_name.value
                        session_id = get_session_id(agent_name.value)
                        await workflow.execute_activity(
                            activities.write_engine_config_for_session,
                            args=[input.repo_path, session_id, engine_name],
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=retry_for("log"),
                        )
                        exploit_input = BlackboxActivityInput(
                            **{**act_input.__dict__,
                               "agent_name": agent_name.value,
                               "vuln_type": vt,
                               "correlation_context": self._state.correlation_context}
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
                await workflow.execute_activity(
                    activities.log_info_activity,
                    BlackboxActivityInput(**{**act_input.__dict__,
                       "info_message": "\n".join(summary_lines),
                       "info_level": "info"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )

                # Track scheduled vuln types for skipped outcomes
                scheduled_vuln_types = {vt for vt, _, _ in exploit_tasks}

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
                            outcomes.append(
                                exploit_result_to_outcome(result, agent_name.value, vt)
                            )

                    # Add skipped outcomes for vuln types that were not scheduled
                    for vt, validation in validation_results:
                        if vt not in scheduled_vuln_types:
                            outcomes.append(AgentOutcome(
                                agent_name=f"{vt}-exploit",
                                vuln_type=vt,
                                status="skipped",
                            ))

                    await workflow.execute_activity(
                        activities.log_info_activity,
                        BlackboxActivityInput(**{**act_input.__dict__,
                           "info_message": format_exploit_summary(outcomes),
                           "info_level": "info"}),
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=retry_for("log"),
                    )

            await workflow.execute_activity(
                activities.log_phase_start_activity,
                args=[
                    BlackboxActivityInput(**{**act_input.__dict__, "phase": "reporting"}),
                    [],
                    [],
                ],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )
            self._state.current_phase = "reporting"
            self._state.current_agent = "assemble-report"
            await workflow.execute_activity(
                activities.assemble_report, act_input,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_policy,
            )
            self._state.current_agent = None

            if AgentName.REPORT.value not in self._state.completed_agents:
                self._state.current_agent = AgentName.REPORT.value
                metrics = await workflow.execute_activity(
                    activities.run_report_agent, act_input,
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry_policy,
                )
                self._state.completed_agents.append(AgentName.REPORT.value)
                self._state.agent_metrics[AgentName.REPORT.value] = metrics
                self._state.current_agent = None

            await workflow.execute_activity(
                activities.finalize_report, act_input,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_policy,
            )

            # Set final status based on failure tracking
            if self._state.failed_agents:
                self._state.status = "failed"
                first_error_msg = self._state.errors[0].split(": ", 1)[-1] if self._state.errors else ""
                error_type, _ = classify_error_for_temporal(Exception(first_error_msg))
                self._state.error_code = error_type
            else:
                self._state.status = "completed"
            self._state.current_phase = None
            if is_worker_path:
                try:
                    await self._finalize_web(act_input)
                except Exception:
                    # 根因 2 加固：落 traceback（不再销毁现场）；仍 best-effort 不阻塞 return
                    # （web _watch finally 兜底补 scan_end）。
                    logger.exception("blackbox _finalize_web failed (best-effort)")
            return self._state
        except CancelledError:
            self._state.status = "cancelled"
            self._state.current_phase = None
            return self._state
        except Exception as e:
            # session-status 同步(对齐 whitebox):workflow-level 失败 → state.status=failed +
            # return(不 raise,Temporal 标 COMPLETED)。不调 finalize_activity(规避
            # finalize_report 签名依赖);session 落盘靠 blackbox CLI worker.py 正常路径
            # (_to_workflow_summary 读 state.status=failed)或其 except Exception 兜底。
            self._state.status = "failed"
            if not self._state.errors:
                self._state.errors.append(f"{type(e).__name__}: {e}")
            self._state.current_phase = None
            if is_worker_path:
                try:
                    await self._finalize_web(act_input)
                except Exception:
                    # 根因 2 加固：落 traceback（不再销毁现场）；仍 best-effort 不阻塞 return
                    # （web _watch finally 兜底补 scan_end）。
                    logger.exception("blackbox _finalize_web failed (best-effort)")
            return self._state
        finally:
            cleanup_settings()
            # engine 对象由 resolve_blackbox_engine activity 持有（workflow 侧不持有不可序列化
            # 对象）；stealth config 清理经 cleanup_engine_configs activity 完成。engine_name 在
            # 上方 try 外由 resolve_blackbox_engine 解析（line 117，先于本 try），此处必已定义。
            if engine_name and input.repo_path:
                try:
                    await workflow.execute_activity(
                        activities.cleanup_engine_configs,
                        args=[input.repo_path, engine_name],
                        # 根因 3：cleanup 对 N session 串行 cleanup_processes（单 session 最坏 ~24s），
                        # 15s 不够；retry 会把同一次超时放大 3×。120s 覆盖串行 + maximum_attempts=1
                        # （cleanup 幂等，残留 config 下次覆盖，失败由 except 吞不阻断收尾）。
                        start_to_close_timeout=timedelta(seconds=120),
                        retry_policy=RetryPolicy(maximum_attempts=1),
                    )
                except Exception:
                    pass  # best-effort cleanup，失败不阻断 workflow 收尾
            try:
                await workflow.execute_activity(
                    activities.cleanup_auth_state_activity,
                    args=[act_input.workspace_path or input.repo_path],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
            except Exception:
                pass  # best-effort cleanup，失败不阻断 workflow 收尾

    def _build_finalize_summary(self, error_fallback: str | None = None) -> dict:
        """构造 finalize_summary 用的 summary dict（success/failed 路径共用，DRY）。

        对齐 whitebox _build_finalize_summary（whitebox/workflows.py:67-88）：agent_metrics 消毒成
        AgentMetricsSummary（只 duration_ms + cost_usd 等 JSON-safe 字段），丢弃 structured_output
        富字段。根因 2（ghost-scan）：裸 dict(self._state.agent_metrics) 含 model_dump 残留的非 JSON
        原生对象 → temporal activity 参数编码失败 → 异常被吞 → finalize_summary 不执行 → scan_end
        不写 / heartbeat 不停 / session 永远 running。status 取 self._state.status（调用方已设）；
        error 取已记录首个 error，无则回落 error_fallback。
        """
        from supernova_core.models.audit import AgentMetricsSummary
        return {
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
            "error": (self._state.errors[0] if self._state.errors else error_fallback),
        }

    async def _finalize_web(self, act_input: BlackboxActivityInput) -> None:
        """C1 Phase B（黑盒 web 化）：worker 路径收尾——调 finalize_summary 写 scan_end 事件 +
        清 AuditSession/heartbeat。仅 is_worker_path 调用（CLI 路径不调，run_scan 外层收尾）。
        summary 经 _build_finalize_summary 消毒（根因 2）。调用处包 best-effort try/except，
        防 finalize 失败阻塞 return（web _watch finally 兜底补 scan_end）。"""
        # 构造与 execute_activity 分离：构造失败时降级最小 summary 继续调度 activity，
        # 保证 scan_end/heartbeat 一定收尾（ghost-scan 兜底）。
        try:
            summary = self._build_finalize_summary()
        except Exception:
            logger.exception("blackbox _build_finalize_summary failed; using minimal summary")
            summary = {
                "status": self._state.status,
                "completed_agents": list(self._state.completed_agents),
            }
        await workflow.execute_activity(
            activities.finalize_summary, args=[act_input, summary],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=retry_for("log"),
        )

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


@workflow.defn
class AuthValidationWorkflow:
    """独立认证验证 workflow(认证管理页"测试登录"):只跑 auth 段,不跑扫描。

    不能复用 BlackboxScanWorkflow:后者强依赖白盒产物(workflows.py:248-280 无白盒 queue
    抛 DELIVERABLE_NOT_FOUND fail-fast)。本 workflow 仅 log_phase + probe + 返回 result。
    """

    @workflow.run
    async def run(self, input: BlackboxAuthValidationInput) -> AuthValidationResult:
        if not input.web_url:
            # non_retryable: 输入校验错误重试无意义(输入不变),对齐 whitebox/workflows.py:475
            # fail-fast 模式;plain ValueError 默认 retryable → workflow task 无限重试,
            # execute_workflow 永久挂起(真机 web "测试登录"漏传 web_url 会卡死)。
            raise ApplicationError("BlackboxAuthValidationInput.web_url is required", non_retryable=True)
        act_input = BlackboxActivityInput(
            web_url=input.web_url,
            config_path=input.config_path,
            workspace_path=input.workspace_path,
            api_key=input.api_key,
            event_file=input.event_file,
        )
        # 块1（认证验证可观测性）：setup_display 挂 AuditSession + StructuredEventRenderer 写
        # events.ndjson（agent 登录每步落盘）。event_file=None（CLI 直调）则不挂 renderer，setup_display
        # 照跑挂 NullAuditSession 兜底。setup_display 自身失败不阻塞验证（降级无 events，spec 风险），
        # 故 try/except 吞掉；但成功后 finalize 必跑（停 heartbeat，否则 daemon 线程泄漏）。
        display_ok = False
        try:
            await workflow.execute_activity(
                activities.setup_display, act_input,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_for("log"),
            )
            display_ok = True
        except Exception:
            pass  # 验证照跑（无 events），NullAuditSession 兜底后续 log_phase
        try:
            # 声明 auth-validation 阶段的 4 步（步骤条）：step key 与 log_milestone 工具同源
            # （AUTH_VALIDATION_PROGRESS），reducer 按 name 匹配推进。steps/intents 经
            # log_phase_start_activity 透传到 PhaseEvent(steps=...)。
            await workflow.execute_activity(
                activities.log_phase_start_activity,
                args=[
                    BlackboxActivityInput(**{**act_input.__dict__,
                                             "phase": AUTH_VALIDATION_PROGRESS.phase}),
                    list(AUTH_VALIDATION_PROGRESS.step_keys),
                    [s.intent for s in AUTH_VALIDATION_PROGRESS.steps],
                ],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )
            return await workflow.execute_activity(
                activities.run_auth_validation_probe, act_input,
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry_for("auth-validation"),
            )
        finally:
            if display_ok:
                # finalize_summary：drain LogBus + log_workflow_complete + 停 heartbeat + 清 session。
                # summary 最小集（auth-validation 无 agent_metrics 聚合，finalize_summary 容错 .get 读）。
                await workflow.execute_activity(
                    activities.finalize_summary,
                    args=[act_input, {"status": "completed"}],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=retry_for("log"),
                )
