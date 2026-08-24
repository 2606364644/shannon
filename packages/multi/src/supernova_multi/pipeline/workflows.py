"""CorrelationScanWorkflow：web 提交的关联阶段（spec 2026-08-24 §5.2）。

形态对齐 whitebox/blackbox 的 pipeline 包；本 workflow 是单 activity 直通
（编排逻辑在 run_correlation_phase，无中间状态机）。
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    from supernova_core.config.parser import parse_multi_repo_config
    from supernova_multi.orchestrator import run_correlation_phase
    from supernova_multi.pipeline.shared import CorrelationPipelineInput


@activity.defn
async def run_correlation_activity(inp: CorrelationPipelineInput) -> dict:
    # env_overrides 走 per-scan 覆盖层（brief「若既有 helper 则复用」）：复用
    # supernova_core.config.scan_env.set_scan_env——与 whitebox/blackbox 的
    # setup_display activity 同一模式。worker 是长驻进程、并发扫描共享 os.environ，
    # 直接 os.environ.update 会互相串台（scan_env 模块的存在理由）；core 的
    # SUPERNOVA_* 读取点经 ws_getenv 命中本覆盖层。单 activity 直通 → finally
    # 清层，等价 whitebox setup_display(set)/finalize(clear) 的生命周期配对。
    from supernova_core.config.scan_env import clear_scan_env, set_scan_env

    set_scan_env(inp.env_overrides)
    try:
        config = parse_multi_repo_config(Path(inp.config_path))
        return await run_correlation_phase(
            config,
            {svc: Path(p) for svc, p in inp.repo_workspace_paths.items()},
            Path(inp.out_ws_dir), Path(inp.event_file),
            pipeline_testing=inp.pipeline_testing,
            provider_config=inp.provider_config,
            write_scan_end=inp.write_scan_end,
        )
    finally:
        clear_scan_env()


@workflow.defn
class CorrelationScanWorkflow:
    @workflow.run
    async def run(self, inp: CorrelationPipelineInput) -> dict:
        return await workflow.execute_activity(
            run_correlation_activity, inp,
            start_to_close_timeout=timedelta(hours=4),
        )
