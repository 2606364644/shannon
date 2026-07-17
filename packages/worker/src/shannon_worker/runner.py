"""常驻 worker 容器入口：连接 temporal，起两个 Worker 消费 WEB 固定 task queue。

与 CLI 的 self-contained run_scan 不同——这里 worker 只消费、不提交：
- 白盒 Worker 消费 shannon-py-wb-web（web scan_manager 提交，Plan 2 接入）
- 黑盒 Worker 消费 shannon-py-bb-web

CLI 路径（shannon-whitebox/-blackbox start）零改动，仍用 generate_task_queue
唯一随机 queue 自己提交自己消费，与本 worker 容器互不干扰（queue 精确匹配）。
"""
import asyncio
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from shannon_core.config.env_loader import load_env
from shannon_core.services.temporal_infra import (
    WEB_TASK_QUEUE_WHITEBOX,
    WEB_TASK_QUEUE_BLACKBOX,
)
from shannon_whitebox.pipeline.workflows import WhiteboxScanWorkflow
from shannon_whitebox.pipeline.activities import (
    render_findings, assemble_report, run_agent,
    run_authz_gitnexus_judge,
    run_code_index, run_credential_check, run_merge_dual_track_queues,
    run_merge_sink_reports, run_entry_point_fusion, run_preflight, run_risk_scoring,
    run_save_adjudication, run_vuln_agent, run_attack_chain_llm_agent,
    run_attack_chain_assembly_v2, run_framework_analysis, run_frontend_mapping,
    run_gitnexus_chain_verdict, run_route_chain_building, generate_poc_report, inject_attack_chains,
    write_track_status_activity,
    log_phase_start_activity, log_phase_complete_activity, log_info_activity,
    setup_display, run_heartbeat, finalize_summary,
)
from shannon_blackbox.pipeline.workflows import BlackboxScanWorkflow
from shannon_blackbox.pipeline.activities import (
    run_blackbox_preflight, run_blackbox_auth_validation, run_recon,
    run_exploit_agent, validate_exploitation_queue, assemble_report as bb_assemble_report,
    run_report_agent, finalize_report, generate_poc_report as bb_generate_poc_report,
    log_phase_start_activity as bb_log_phase_start, log_phase_complete_activity as bb_log_phase_complete,
    log_info_activity as bb_log_info, load_correlation_context, resolve_blackbox_engine,
    detect_whitebox_results, write_engine_config_for_session, cleanup_engine_configs,
)

_GRACEFUL_SHUTDOWN = timedelta(seconds=10)


async def run_worker(temporal_address: str = "localhost:7233") -> None:
    """连接 temporal，起白盒+黑盒两个常驻 Worker 并行消费 WEB 固定 queue。

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
            write_track_status_activity,
            log_phase_start_activity, log_phase_complete_activity, log_info_activity,
            setup_display, run_heartbeat, finalize_summary,
        ],
        # AuditSession 是进程全局 _current 单例(session_registry.py), events.ndjson 经它写.
        # worker 容器并发多白盒扫描会冲突 → 白盒 Worker 并发=1. 解锁需把 AuditSession 改
        # contextvar(更大重构, 留 follow-up, 不在本 plan).
        max_concurrent_workflow_tasks=1,
        graceful_shutdown_timeout=_GRACEFUL_SHUTDOWN,
    )
    bb_worker = Worker(
        client=client,
        task_queue=WEB_TASK_QUEUE_BLACKBOX,
        workflows=[BlackboxScanWorkflow],
        activities=[
            run_blackbox_preflight, run_blackbox_auth_validation, run_recon,
            run_exploit_agent, validate_exploitation_queue, bb_assemble_report,
            run_report_agent, finalize_report, bb_generate_poc_report,
            bb_log_phase_start, bb_log_phase_complete, bb_log_info,
            load_correlation_context, resolve_blackbox_engine, detect_whitebox_results,
            write_engine_config_for_session, cleanup_engine_configs,
        ],
        # 对齐 wb_worker: AuditSession 全局单例 + LogBus 单例多 scan 并发会冲突/串台
        # (runner.py:68-71 注释同因)。黑盒 web 扫描 C1 化(Plan B)前先就位。
        max_concurrent_workflow_tasks=1,
        graceful_shutdown_timeout=_GRACEFUL_SHUTDOWN,
    )

    await asyncio.gather(wb_worker.run(), bb_worker.run())


def main() -> None:
    # 加载共享 .env + 当前 profile 凭证(SHANNON_AI_PROVIDER / SHANNON_OPENAI_* 等)。
    # worker 容器化时漏了这一步:不调 load_env,profile 凭证不进进程环境 → 引擎回落
    # 默认 anthropic_api(claude CLI)→ deepseek-openai 等 openai profile 无 ANTHROPIC
    # 凭证 → claude CLI 子进程 "Not logged in · Please run /login" → pre-recon 失败、
    # 扫描卡死、WEB 各页全空(真机 trip_1784107863)。对齐 CLI 入口(whitebox/
    # blackbox/combined main.py 均首行 load_env)。
    load_env()
    import os
    host = os.environ.get("SHANNON_TEMPORAL_HOST", "localhost")
    port = os.environ.get("SHANNON_TEMPORAL_PORT", "7233")
    asyncio.run(run_worker(f"{host}:{port}"))
