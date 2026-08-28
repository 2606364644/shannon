"""常驻 worker 容器入口：连接 temporal，起三个 Worker 消费 WEB 固定 task queue。

与 CLI 的 self-contained run_scan 不同——这里 worker 只消费、不提交：
- 白盒 Worker 消费 supernova-wb-web（web scan_manager 提交，Plan 2 接入）
- 黑盒 Worker 消费 supernova-bb-web
- 跨仓关联 Worker 消费 supernova-corr-web（B2：依赖 supernova-multi pipeline）

CLI 路径（supernova-whitebox/-blackbox start）零改动，仍用 generate_task_queue
唯一随机 queue 自己提交自己消费，与本 worker 容器互不干扰（queue 精确匹配）。
"""
import asyncio
import os
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from supernova_core.config.env_loader import load_env
from supernova_core.services.temporal_infra import (
    WEB_TASK_QUEUE_WHITEBOX,
    WEB_TASK_QUEUE_BLACKBOX,
    WEB_TASK_QUEUE_CORRELATION,
)
from supernova_whitebox.pipeline.workflows import WhiteboxScanWorkflow
from supernova_whitebox.pipeline.activities import (
    render_findings, assemble_report, run_agent,
    run_authz_gitnexus_judge,
    run_code_index, run_credential_check, run_merge_dual_track_queues,
    run_gn_finding_enrichment, run_endpoint_enrichment, run_report_polish,
    run_merge_sink_reports, run_entry_point_fusion, run_preflight, run_risk_scoring,
    run_save_adjudication, run_vuln_agent, run_attack_chain_llm_agent,
    run_attack_chain_assembly_v2, run_framework_analysis, run_frontend_mapping,
    run_gitnexus_chain_verdict, run_route_chain_building, inject_attack_chains,
    inject_gitnexus_track_status, write_agent_poc,
    run_assemble_dataflow_view,
    verify_report_vuln_blocks,
    export_report_markdown_files,
    write_track_status_activity,
    log_phase_start_activity, log_phase_complete_activity, log_info_activity,
    setup_display, finalize_summary, cleanup_auth_state_activity,
    persist_completed_agents,
)
from supernova_blackbox.pipeline.workflows import BlackboxScanWorkflow, AuthValidationWorkflow, BatchAuthValidationWorkflow
from supernova_blackbox.pipeline.activities import (
    run_blackbox_preflight, run_blackbox_auth_validation,
    run_auth_validation_probe,
    run_exploit_agent, run_endpoint_verify, validate_exploitation_queue, assemble_report as bb_assemble_report,
    run_report_agent, finalize_report,
    log_phase_start_activity as bb_log_phase_start, log_phase_complete_activity as bb_log_phase_complete,
    log_info_activity as bb_log_info, load_correlation_context, resolve_blackbox_engine,
    detect_whitebox_results, write_engine_config_for_session, cleanup_engine_configs,
    verify_report_vuln_blocks as bb_verify_report_vuln_blocks,
    setup_display as bb_setup_display, finalize_summary as bb_finalize_summary,
    run_host_proxy_setup as bb_run_host_proxy_setup, stop_host_proxy as bb_stop_host_proxy,
    cleanup_auth_state_activity as bb_cleanup_auth_state_activity,
    persist_completed_agents as bb_persist_completed_agents,
)
from supernova_multi.pipeline.workflows import CorrelationScanWorkflow, run_correlation_activity
from supernova_core.runtime.heartbeat import snapshot_heartbeat_workflows

_GRACEFUL_SHUTDOWN = timedelta(seconds=10)
# 心跳节流收紧（spec 2026-08-28-temporal-native-cancel-design 修 F）：temporalio 默认
# default_heartbeat_throttle_interval=30s——activity 不设 heartbeat_timeout 时每 30s 才真发
# 一次心跳 RPC，取消传播上限被拖到 30s+。收紧到 10s → web Cancel 后 ~10s 级送达 activity。
_HEARTBEAT_THROTTLE = timedelta(seconds=10)

# 协作取消桥轮询周期（2026-08-28 取消失效治本方案 B）。exists() 检查极廉，
# 取短周期换「点取消 → worker 真停」的低延迟。
_CANCEL_BRIDGE_INTERVAL_SECONDS = 5.0


async def _process_cancel_signals(client: Client) -> None:
    """单轮协作取消桥：扫活跃 heartbeat 注册表各 ws_dir 的 cancel.requested。

    web cancel ② 轨写 cancel.requested，但 worker 容器路径的 activity
    start_heartbeat(on_cancel=None) 不消费协作信号（只有 CLI 路径 HeartbeatManager
    挂 ctrl._trigger_graceful）——owner=web 扫描的协作通道整个不存在（死信）。
    本桥把文件信号转回 temporal cancel，复用 temporalio 对 async activity 的
    task.cancel 传导（_activity.py:762-764，无需 heartbeat）。

    信号文件先删再 cancel（防下一轮对已终态 workflow 重复触发）；cancel 抛错
    （workflow 不存在/已终态/temporal 抖动）best-effort 吞掉。CLI 路径不受影响
    （随机 task queue 隔离，其 HeartbeatManager 自消费信号）。
    """
    for wf_id, ws_dir in snapshot_heartbeat_workflows().items():
        sig = ws_dir / "cancel.requested"
        if not sig.exists():
            continue
        try:
            sig.unlink()
        except OSError:
            continue  # 删失败（权限/竞态）跳过本轮，下轮重试
        try:
            await client.get_workflow_handle(wf_id).cancel()
        except Exception:  # noqa: BLE001 - best-effort；信号已删，不重复触发
            pass


async def _cancel_signal_bridge(client: Client) -> None:
    """协作取消桥主循环（worker 容器常驻后台 task，进程退出即止）。"""
    while True:
        await asyncio.sleep(_CANCEL_BRIDGE_INTERVAL_SECONDS)
        try:
            await _process_cancel_signals(client)
        except Exception:  # noqa: BLE001 - 桥绝不因单轮异常退出
            pass


async def run_worker(temporal_address: str = "localhost:7233") -> None:
    """连接 temporal，起白盒+黑盒+跨仓关联三个常驻 Worker 并行消费 WEB 固定 queue。

    永不主动返回（常驻）；temporal 连接失败 fail-fast 抛错。
    """
    client = await Client.connect(temporal_address)

    wb_worker = Worker(
        client=client,
        task_queue=WEB_TASK_QUEUE_WHITEBOX,
        workflows=[WhiteboxScanWorkflow],
        activities=[
            render_findings, assemble_report, run_agent,
            run_authz_gitnexus_judge,
            run_code_index, run_credential_check, run_merge_dual_track_queues,
            run_gn_finding_enrichment, run_endpoint_enrichment, run_report_polish,
            run_merge_sink_reports, run_entry_point_fusion, run_preflight, run_risk_scoring,
            run_save_adjudication, run_vuln_agent, run_attack_chain_llm_agent,
            run_attack_chain_assembly_v2, run_framework_analysis, run_frontend_mapping,
            run_gitnexus_chain_verdict, run_route_chain_building, inject_attack_chains,
            inject_gitnexus_track_status, write_agent_poc,
            run_assemble_dataflow_view,
            verify_report_vuln_blocks,
            export_report_markdown_files,
            write_track_status_activity,
            log_phase_start_activity, log_phase_complete_activity, log_info_activity,
            setup_display, finalize_summary, cleanup_auth_state_activity,
            persist_completed_agents,
        ],
        # P3c 阶段 3：AuditSession/LogBus/heartbeat 已 contextvar 化（按 workflow_id 隔离），
        # 多 scan 并发不再串台 → max_concurrent 放开（默认 4，env 可配）。
        max_concurrent_workflow_tasks=int(
            os.environ.get("SUPERNOVA_WORKER_MAX_CONCURRENT_WF", "4")
        ),
        graceful_shutdown_timeout=_GRACEFUL_SHUTDOWN,
        default_heartbeat_throttle_interval=_HEARTBEAT_THROTTLE,
    )
    bb_worker = Worker(
        client=client,
        task_queue=WEB_TASK_QUEUE_BLACKBOX,
        workflows=[BlackboxScanWorkflow, AuthValidationWorkflow, BatchAuthValidationWorkflow],
        activities=[
            run_blackbox_preflight, run_blackbox_auth_validation,
            run_auth_validation_probe,
            run_exploit_agent, run_endpoint_verify, validate_exploitation_queue, bb_assemble_report,
            run_report_agent, finalize_report,
            bb_log_phase_start, bb_log_phase_complete, bb_log_info,
            load_correlation_context, resolve_blackbox_engine, detect_whitebox_results,
            write_engine_config_for_session, cleanup_engine_configs,
            bb_setup_display, bb_finalize_summary,
            bb_run_host_proxy_setup, bb_stop_host_proxy,
            bb_cleanup_auth_state_activity,
            bb_persist_completed_agents,
            bb_verify_report_vuln_blocks,
            bb_persist_completed_agents,
        ],
        # P3c 阶段 3：对齐 wb_worker，contextvar 化后并发放开（默认 4，env 可配）。
        max_concurrent_workflow_tasks=int(
            os.environ.get("SUPERNOVA_WORKER_MAX_CONCURRENT_WF", "4")
        ),
        graceful_shutdown_timeout=_GRACEFUL_SHUTDOWN,
        default_heartbeat_throttle_interval=_HEARTBEAT_THROTTLE,
    )
    corr_worker = Worker(
        client=client,
        task_queue=WEB_TASK_QUEUE_CORRELATION,
        workflows=[CorrelationScanWorkflow],
        activities=[run_correlation_activity],
        # 对齐 wb/bb worker，contextvar 化后并发放开（默认 4，env 可配）。
        max_concurrent_workflow_tasks=int(
            os.environ.get("SUPERNOVA_WORKER_MAX_CONCURRENT_WF", "4")
        ),
        graceful_shutdown_timeout=_GRACEFUL_SHUTDOWN,
        default_heartbeat_throttle_interval=_HEARTBEAT_THROTTLE,
    )

    # 协作取消桥（方案 B）：把 web cancel ② 轨的 cancel.requested 文件信号转发为
    # temporal cancel（worker 容器路径协作通道的唯一消费者）。worker 全退时一并取消。
    bridge = asyncio.create_task(_cancel_signal_bridge(client))
    try:
        await asyncio.gather(wb_worker.run(), bb_worker.run(), corr_worker.run())
    finally:
        bridge.cancel()


def main() -> None:
    # 加载共享 .env + 当前 profile 凭证(SUPERNOVA_AI_PROVIDER / SUPERNOVA_OPENAI_* 等)。
    # worker 容器化时漏了这一步:不调 load_env,profile 凭证不进进程环境 → 引擎回落
    # 默认 anthropic_api(claude CLI)→ deepseek-openai 等 openai profile 无 ANTHROPIC
    # 凭证 → claude CLI 子进程 "Not logged in · Please run /login" → pre-recon 失败、
    # 扫描卡死、WEB 各页全空(真机 trip_1784107863)。对齐 CLI 入口(whitebox/
    # blackbox/combined main.py 均首行 load_env)。
    load_env()
    import os
    host = os.environ.get("SUPERNOVA_TEMPORAL_HOST", "localhost")
    port = os.environ.get("SUPERNOVA_TEMPORAL_PORT", "7233")
    asyncio.run(run_worker(f"{host}:{port}"))
