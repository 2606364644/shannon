import asyncio
import logging
from datetime import timedelta
from pathlib import Path

from temporalio import workflow
from temporalio.exceptions import CancelledError

from supernova_core.models.agents import AgentName, ALL_VULN_CLASSES
from supernova_core.utils.paths import (
    resolve_deliverables_path,
    has_valid_whitebox_results,
    resolve_track_deliverable,
    WHITEBOX_SUBDIR,
)

from .shared import BlackboxActivityInput, BlackboxPipelineInput, BlackboxPipelineState, PipelineProgress

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
    from supernova_core.services.validate_authentication import cleanup_auth_state_sync
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

        # Compute workspace_path consistent with whitebox (workspaces/<name>/)
        if input.workspace_name:
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
        )

        retry_policy = retry_for(
            "standard",
            "testing" if input.pipeline_testing_mode else (input.retry_profile or "production"),
        )

        await workflow.execute_activity(
            activities.log_phase_start_activity,
            BlackboxActivityInput(**{**act_input.__dict__, "phase": "preflight"}),
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
                BlackboxActivityInput(**{**act_input.__dict__, "phase": "auth-validation"}),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )
            await workflow.execute_activity(
                activities.run_blackbox_auth_validation, act_input,
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry_for("auth-validation"),
            )

        try:
            # Resolve deliverables path using shared utility
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
            wb = await workflow.execute_activity(
                activities.detect_whitebox_results,
                args=[str(deliverables), selected_classes, corr_dlv_path],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_for("log"),
            )
            has_whitebox_results: bool = wb["has_whitebox_results"]
            found_classes: list[str] = wb["found_classes"]
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

            if has_whitebox_results:
                await workflow.execute_activity(
                    activities.log_info_activity,
                    BlackboxActivityInput(**{**act_input.__dict__,
                       "info_message": f"Whitebox results detected at {deliverables} for classes: {found_classes} — skipping RECON_BLACKBOX",
                       "info_level": "info"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
            else:
                await workflow.execute_activity(
                    activities.log_info_activity,
                    BlackboxActivityInput(**{**act_input.__dict__,
                       "info_message": f"No whitebox results found at {deliverables} — running RECON_BLACKBOX from scratch. Tip: pass --repo <path> to reuse whitebox scan results.",
                       "info_level": "warning"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )

            if not has_whitebox_results and AgentName.RECON_BLACKBOX.value not in self._state.completed_agents:
                await workflow.execute_activity(
                    activities.log_phase_start_activity,
                    BlackboxActivityInput(**{**act_input.__dict__, "phase": "recon-blackbox"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
                self._state.current_phase = "recon-blackbox"
                self._state.current_agent = AgentName.RECON_BLACKBOX.value
                recon_input = BlackboxActivityInput(**{**act_input.__dict__})
                metrics = await workflow.execute_activity(
                    activities.run_recon, recon_input,
                    start_to_close_timeout=timedelta(hours=2),
                    retry_policy=retry_policy,
                )
                self._state.completed_agents.append(AgentName.RECON_BLACKBOX.value)
                self._state.agent_metrics[AgentName.RECON_BLACKBOX.value] = metrics
                self._state.current_agent = None

            if input.exploit:
                await workflow.execute_activity(
                    activities.log_phase_start_activity,
                    BlackboxActivityInput(**{**act_input.__dict__, "phase": "exploitation"}),
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=retry_for("log"),
                )
                self._state.current_phase = "exploitation"
                self._state.current_agent = "pipelines"
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
                BlackboxActivityInput(**{**act_input.__dict__, "phase": "reporting"}),
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

            # === 报告增强：生成 PoC md（失败由 activity 吞掉） ===
            try:
                # §8 契约硬化:PoC 非关键报告增强,timeout/ActivityError 绝不阻塞主流程
                # (activity 内 try/except 抓不到 Temporal runtime cancel)。
                await workflow.execute_activity(
                    activities.generate_poc_report, act_input,
                    start_to_close_timeout=timedelta(minutes=20),
                    retry_policy=retry_for("poc"),
                )
            except Exception:  # noqa: BLE001 — PoC 任何失败(含 ActivityError)只降级
                pass

            # Set final status based on failure tracking
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
        except Exception as e:
            # session-status 同步(对齐 whitebox):workflow-level 失败 → state.status=failed +
            # return(不 raise,Temporal 标 COMPLETED)。不调 finalize_activity(规避
            # finalize_report 签名依赖);session 落盘靠 blackbox CLI worker.py 正常路径
            # (_to_workflow_summary 读 state.status=failed)或其 except Exception 兜底。
            self._state.status = "failed"
            if not self._state.errors:
                self._state.errors.append(f"{type(e).__name__}: {e}")
            self._state.current_phase = None
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
                        start_to_close_timeout=timedelta(seconds=15),
                        retry_policy=retry_for("log"),
                    )
                except Exception:
                    pass  # best-effort cleanup，失败不阻断 workflow 收尾
            cleanup_auth_state_sync(act_input.workspace_path or input.repo_path)

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
