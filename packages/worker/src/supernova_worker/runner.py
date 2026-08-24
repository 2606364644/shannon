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
    run_merge_sink_reports, run_entry_point_fusion, run_preflight, run_risk_scoring,
    run_save_adjudication, run_vuln_agent, run_attack_chain_llm_agent,
    run_attack_chain_assembly_v2, run_framework_analysis, run_frontend_mapping,
    run_gitnexus_chain_verdict, run_route_chain_building, generate_poc_report, inject_attack_chains,
    inject_gitnexus_track_status,
    run_assemble_dataflow_view,
    verify_report_vuln_blocks,
    write_track_status_activity,
    log_phase_start_activity, log_phase_complete_activity, log_info_activity,
    setup_display, finalize_summary, cleanup_auth_state_activity,
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
)
from supernova_multi.pipeline.workflows import CorrelationScanWorkflow, run_correlation_activity

_GRACEFUL_SHUTDOWN = timedelta(seconds=10)


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
            run_merge_sink_reports, run_entry_point_fusion, run_preflight, run_risk_scoring,
            run_save_adjudication, run_vuln_agent, run_attack_chain_llm_agent,
            run_attack_chain_assembly_v2, run_framework_analysis, run_frontend_mapping,
            run_gitnexus_chain_verdict, run_route_chain_building, generate_poc_report, inject_attack_chains,
            inject_gitnexus_track_status,
            run_assemble_dataflow_view,
            verify_report_vuln_blocks,
            write_track_status_activity,
            log_phase_start_activity, log_phase_complete_activity, log_info_activity,
            setup_display, finalize_summary, cleanup_auth_state_activity,
        ],
        # P3c 阶段 3：AuditSession/LogBus/heartbeat 已 contextvar 化（按 workflow_id 隔离），
        # 多 scan 并发不再串台 → max_concurrent 放开（默认 4，env 可配）。
        max_concurrent_workflow_tasks=int(
            os.environ.get("SUPERNOVA_WORKER_MAX_CONCURRENT_WF", "4")
        ),
        graceful_shutdown_timeout=_GRACEFUL_SHUTDOWN,
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
            bb_verify_report_vuln_blocks,
        ],
        # P3c 阶段 3：对齐 wb_worker，contextvar 化后并发放开（默认 4，env 可配）。
        max_concurrent_workflow_tasks=int(
            os.environ.get("SUPERNOVA_WORKER_MAX_CONCURRENT_WF", "4")
        ),
        graceful_shutdown_timeout=_GRACEFUL_SHUTDOWN,
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
    )

    await asyncio.gather(wb_worker.run(), bb_worker.run(), corr_worker.run())


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
