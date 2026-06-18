import asyncio
import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from shannon_core.models.audit import WorkflowSummary, AgentMetricsSummary

from .pipeline.activities import (
    render_findings,
    run_agent,
    run_auth_validation,
    run_code_index,
    run_credential_check,
    run_merge_sink_reports,
    run_entry_point_fusion,
    run_preflight,
    run_render_dataflow_hints,
    run_risk_scoring,
    run_save_adjudication,
    run_vuln_agent,
    run_attack_chain_assembly,
    run_framework_analysis,
    run_frontend_mapping,
    run_route_chain_building,
    log_phase_start_activity,
    log_phase_complete_activity,
)
from .pipeline.workflows import WhiteboxScanWorkflow
from .pipeline.shared import PipelineInput
from shannon_core.utils.paths import resolve_workspaces_dir
from shannon_core.services.temporal_infra import generate_task_queue
from shannon_core.runtime.scan_runner import (
    ScanCancelled,
    ShutdownController,
    await_workflow_with_shutdown,
)

TASK_QUEUE_PREFIX = "shannon-py-wb"


def resolve_workflow_id(
    workspace_name: str | None, epoch: float, resume_attempt: int = 0
) -> str:
    """Single source of truth for the Temporal workflow id.

    Used for both the WorkflowHeader banner (meta.id → web_ui_url / logs_cmd)
    and client.start_workflow(id=...) so the Web UI link points at the real run.

    resume_attempt > 0 且有 workspace_name 时追加 ``-resume-{n}``，规避与旧
    workflow 的 Temporal ``AlreadyStarted`` 冲突。默认 0 保证向后兼容。
    """
    if resume_attempt > 0 and workspace_name:
        return f"{workspace_name}-resume-{resume_attempt}"
    return workspace_name or f"whitebox-{int(epoch)}"


async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233",
                   use_rich: bool = False) -> dict:
    from shannon_core.session import SessionManager
    from shannon_core.models.metrics import SessionMetadata
    from shannon_whitebox.audit.display_lifecycle import run_with_display

    # Persist session data so blackbox can discover repo_path
    if input.workspace_name:
        workspaces_dir = resolve_workspaces_dir(input.repo_path)
        mgr = SessionManager(workspaces_dir)
        mgr.create_workspace(
            web_url=input.web_url or "",
            repo_path=input.repo_path,
            name=input.workspace_name,
        )

    client = await Client.connect(temporal_address)
    task_queue = generate_task_queue(TASK_QUEUE_PREFIX)

    worker = Worker(
        client=client,
        task_queue=task_queue,
        workflows=[WhiteboxScanWorkflow],
        activities=[
            render_findings, run_agent, run_auth_validation, run_code_index,
            run_credential_check, run_merge_sink_reports, run_entry_point_fusion,
            run_preflight, run_render_dataflow_hints, run_risk_scoring,
            run_save_adjudication, run_vuln_agent, run_attack_chain_assembly,
            run_framework_analysis, run_frontend_mapping, run_route_chain_building,
            log_phase_start_activity, log_phase_complete_activity,
        ],
    )

    loop = asyncio.get_running_loop()

    # resume 探测：从磁盘重建 completed_agents，激活 workflow 的空壳守卫。
    # fresh 模式（input._fresh=True）或无 workspace_name 跳过整个探测块。
    # None = 尚未计算（fresh/首次/无 completed_agents）；>=1 = resume 第 N 次。
    # 用哨兵而非 0，避免下方 "if not resume_attempt" 把 0 当 falsy 与"未设置"混淆。
    resume_attempt = 0
    workflow_id: str | None = None
    is_fresh = bool(getattr(input, "_fresh", False))
    if input.workspace_name and not is_fresh:
        from shannon_whitebox.pipeline.whitebox_resume import (
            WhiteboxResumeStateBuilder,
        )
        from shannon_core.utils.paths import resolve_deliverables_path

        workspaces_dir = resolve_workspaces_dir(input.repo_path)
        ws_dir = workspaces_dir / input.workspace_name
        deliverables = resolve_deliverables_path(
            input.repo_path, input.deliverables_subdir, input.workspace_name,
        )
        rewind_target = getattr(input, "_rewind_target", None)
        mode = "rewind" if rewind_target else "auto"

        builder = WhiteboxResumeStateBuilder()
        rstate = await builder.build(
            mode=mode,
            workspace=ws_dir,
            deliverables=deliverables,
            repo_path=Path(input.repo_path),
            rewind_target=rewind_target,
        )
        if rstate.aborted:
            raise RuntimeError(rstate.abort_reason)

        if rstate.completed_agents:
            input.resume_completed_agents = rstate.completed_agents

            # === top-level `resumeAttempts` schema 说明 ===
            # worker 把 resume 计数记录在 session.json 的 **top-level** `resumeAttempts`
            #（与 repo_path / web_url / created_at 同层）。这是 worker resume 逻辑的
            # canonical 位置：resume_attempt = len(top-level resumeAttempts) + 1，
            # resume 成功后向它 append 一条。它与 MetricsTracker 拥有的嵌套
            # `session.resumeAttempts`（audit/UI 显示用，被 initialize 重置、目前由
            # add_resume_attempt 写入）刻意分离 —— 不同所有者、不同生命周期。
            # 跨 MetricsTracker.initialize 的存活依赖 initialize 用 `dict(existing)`
            # 浅拷贝保留所有 top-level key（MetricsTracker 只 owns `session`/`metrics`
            # 两个子树）。见 test_resume_attempts_survive_metrics_tracker_initialize。

            # 决策 2：resume_attempt 从 session.json 的 top-level resumeAttempts 读 len+1
            session_file = ws_dir / "session.json"
            n = 1
            if session_file.exists():
                try:
                    existing = json.loads(
                        session_file.read_text(encoding="utf-8"))
                    attempts = existing.get("resumeAttempts") or []
                    if isinstance(attempts, list):
                        n = len(attempts) + 1
                except (json.JSONDecodeError, OSError):
                    n = 1
            resume_attempt = n

            # 决策 5：rewind cleanup 需要 run_ts 做归档目录名
            cleanup_kwargs = dict(
                mode=mode,
                deliverables=deliverables,
                completed_agents=rstate.completed_agents,
                rewind_target=rewind_target,
            )
            if mode == "rewind":
                cleanup_kwargs["run_ts"] = datetime.now().strftime(
                    "%Y%m%d-%H%M%S")
            await builder.cleanup(**cleanup_kwargs)

            # 决策 4：resume 成功后追加一条到 top-level resumeAttempts。
            # workflow_id 只算一次（含 resume_attempt），preview 与下方实际
            # start_workflow(id=...) 复用同一个值。
            workflow_id = resolve_workflow_id(
                input.workspace_name, loop.time(), resume_attempt)
            terminated = (
                [rstate.interrupted_agent] if rstate.interrupted_agent else []
            )
            self_mgr = SessionManager(workspaces_dir)
            ws_path = self_mgr.get_workspace(input.workspace_name)
            if ws_path is not None:
                data = self_mgr.get_session_data(ws_path)
                attempts = data.get("resumeAttempts") or []
                attempts.append({
                    "workflowId": workflow_id,
                    "terminatedAgents": terminated,
                    "checkpoint": None,
                })
                data["resumeAttempts"] = attempts
                self_mgr.update_session(ws_path, data)

    # 非恢复路径（fresh/首次/无 completed_agents）：resume_attempt 仍为 0，
    # workflow_id 尚未在恢复分支内计算，这里补算。恢复路径已在上方算过，直接复用。
    if workflow_id is None:
        workflow_id = resolve_workflow_id(
            input.workspace_name, loop.time(), resume_attempt)

    # 决策 1：meta.id 固定 = workspace_name（若有），保证 MetricsTracker 写
    # session.json 的目录 = workspace_name/，与 builder 下次 resume 读取路径一致。
    # resume 时 workflow_id 会变 workspace_name-resume-N，但 session.json 必须仍
    # 累积在 workspace_name/ 下。fresh/首次扫描（有 workspace_name）行为不变。
    session_id = input.workspace_name or workflow_id

    meta = SessionMetadata(
        id=session_id,
        web_url=input.web_url,
        repo_path=input.repo_path,
        output_path=str(resolve_workspaces_dir(input.repo_path)),
    )

    ctrl = ShutdownController()
    ctrl.install(asyncio.get_running_loop())
    try:
        async with worker:
            async with run_with_display(meta, use_rich=use_rich) as session:
                from shannon_whitebox.audit.session_registry import (
                    set_audit_session, clear_audit_session,
                )
                set_audit_session(session)
                scan_start = time.monotonic()
                try:
                    handle = await client.start_workflow(
                        WhiteboxScanWorkflow.run,
                        input,
                        id=workflow_id,
                        task_queue=task_queue,
                    )
                    try:
                        # progress_type=None: Rich 仪表盘已负责进度展示，不重复 poll 打印。
                        result = await await_workflow_with_shutdown(
                            handle, ctrl, cancel_grace_seconds=15.0,
                        )
                    except ScanCancelled:
                        return {"status": "cancelled"}
                finally:
                    clear_audit_session()

                # Emit the final summary so the Rich summary table / dashboard
                # finalization fires. Build it from the PipelineState result.
                status = result.status if isinstance(result.status, str) and result.status in ("completed", "failed", "cancelled") else "failed"
                agent_metrics_summary = {
                    name: AgentMetricsSummary(
                        duration_ms=int(m.get("duration_ms", 0) or 0),
                        cost_usd=m.get("cost_usd"),
                    )
                    for name, m in (result.agent_metrics or {}).items()
                }
                summary = WorkflowSummary(
                    status=status,
                    total_duration_ms=int((time.monotonic() - scan_start) * 1000),
                    total_cost_usd=sum((am.cost_usd or 0.0) for am in agent_metrics_summary.values()),
                    completed_agents=list(result.completed_agents or []),
                    agent_metrics=agent_metrics_summary,
                    error=(result.errors[0] if result.errors else None),
                )
                await session.log_workflow_complete(summary)

                result_dict = asdict(result) if not isinstance(result, dict) else dict(result)
                result_dict["workspace_name"] = input.workspace_name
                result_dict["web_url"] = input.web_url

                # Deliverables always live repo-centric (<repo>/<subdir>), matching
                # where whitebox activities write — independent of workspace_name.
                result_dict["deliverables_path"] = str(
                    Path(input.repo_path) / input.deliverables_subdir)
                return result_dict
    finally:
        ctrl.uninstall()


def main():
    import sys
    asyncio.run(run_scan(PipelineInput(repo_path=sys.argv[1] if len(sys.argv) > 1 else ".")))
