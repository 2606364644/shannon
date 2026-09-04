"""Orchestration logic: runs whitebox scan then blackbox scan in sequence."""

from supernova_blackbox.pipeline.shared import BlackboxPipelineInput
from supernova_core.config.concurrency import get_max_concurrent
from supernova_whitebox.pipeline.shared import PipelineInput


async def run_whitebox_scan(input: PipelineInput, temporal_address: str) -> dict:
    """Run whitebox scan and return result dict."""
    from supernova_whitebox.worker import run_scan
    return await run_scan(input, temporal_address)


async def run_blackbox_scan(input: BlackboxPipelineInput, temporal_address: str):
    """Run blackbox scan and return result."""
    from supernova_blackbox.worker import run_scan
    return await run_scan(input, temporal_address)


async def run_combined_scan(
    repo_path: str,
    url: str,
    temporal_address: str = "localhost:7233",
    config_path: str | None = None,
    pipeline_testing: bool = False,
) -> dict:
    """Run whitebox → blackbox in sequence.

    Returns the final blackbox result, or the whitebox result if whitebox failed.
    """
    # Phase 1: Whitebox
    wb_input = PipelineInput(
        repo_path=repo_path,
        web_url=url,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
        max_concurrent=get_max_concurrent(),
    )

    wb_result = await run_whitebox_scan(wb_input, temporal_address)

    if wb_result.get("status") == "cancelled":
        return {"status": "cancelled", "phase": "whitebox"}

    if wb_result.get("status") != "completed":
        return {
            "status": "failed",
            "phase": "whitebox",
            "error": wb_result.get("error", "whitebox scan failed"),
        }

    workspace_name = wb_result.get("workspace_name")
    if not workspace_name:
        return {
            "status": "failed",
            "phase": "whitebox",
            "error": "whitebox completed but no workspace_name returned",
        }

    # Phase 2: Blackbox — reuse whitebox workspace
    bb_input = BlackboxPipelineInput(
        web_url=url,
        repo_path=repo_path,
        workspace_name=workspace_name,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
        max_concurrent=get_max_concurrent(),
    )

    bb_result = await run_blackbox_scan(bb_input, temporal_address)

    # Convert dataclass result to dict if needed
    if hasattr(bb_result, "__dataclass_fields__"):
        from dataclasses import asdict
        bb_dict = asdict(bb_result)
    else:
        bb_dict = bb_result if isinstance(bb_result, dict) else {"status": str(bb_result)}

    bb_dict["whitebox_workspace"] = workspace_name
    # T8（spec 2026-08-26-report-generation-agent §6.2）：黑盒完成后融合报告——
    # 白盒 rd.json × 黑盒 rd.json → fuse（cross_verification 三态 + gaps）→
    # combined/run-K/report_data.json + combined_report.md 导出。non-fatal。
    if bb_dict.get("status") == "completed":
        try:
            await _generate_fusion_report(repo_path, bb_dict)
        except Exception as exc:  # noqa: BLE001 — 融合失败不阻塞组合扫描收尾
            import logging
            logging.getLogger(__name__).warning(
                "combined fusion report failed (non-fatal): %s", exc)
    return bb_dict


async def _generate_fusion_report(repo_path: str, bb_dict: dict) -> None:
    """CLI 组合路径的融合报告（对齐 web _generate_combined_report 主路径）。"""
    from pathlib import Path

    from supernova_core.models.report_data import ScanMeta
    from supernova_core.services import report_data_builder
    from supernova_core.services.report_data_blackbox import (
        build_blackbox_report_data,
        build_class_meta,
    )
    from supernova_core.services.report_fusion import fuse_report_data
    from supernova_core.services.report_markdown_exporter import (
        export_report_markdown,
    )
    from supernova_core.utils.paths import blackbox_run_dir, combined_run_dir, whitebox_dir

    workspace_name = bb_dict.get("whitebox_workspace") or ""
    run_id = bb_dict.get("run_id") or bb_dict.get("workspace_name") or "run-1"
    scan_dir = Path(repo_path).parent / workspace_name
    run_dir = blackbox_run_dir(scan_dir, run_id)
    scan_meta = ScanMeta(id=scan_dir.name, track="whitebox")
    wb_rd = await report_data_builder.build_report_data(
        whitebox_dir(scan_dir / "deliverables"), scan_meta)
    bb_rd = await build_blackbox_report_data(
        run_dir / "deliverables",
        scan_meta.model_copy(update={"track": "blackbox"}))
    # not-covered 成因判据（spec 2026-09-03 §6）：各类 verdicts 存在性 + 验证范围
    class_meta = await build_class_meta(run_dir / "deliverables")
    fused = fuse_report_data(wb_rd, bb_rd, blackbox_class_meta=class_meta)
    out_dir = combined_run_dir(scan_dir, run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    await report_data_builder.write_report_data(
        fused, out_dir / "report_data.json")
    (out_dir / "combined_report.md").write_text(
        export_report_markdown(fused), encoding="utf-8")
