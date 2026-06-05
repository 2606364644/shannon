"""Orchestration logic: runs whitebox scan then blackbox scan in sequence."""

from shannon_blackbox.pipeline.shared import BlackboxPipelineInput
from shannon_whitebox.pipeline.shared import PipelineInput


async def run_whitebox_scan(input: PipelineInput, temporal_address: str) -> dict:
    """Run whitebox scan and return result dict."""
    from shannon_whitebox.worker import run_scan
    return await run_scan(input, temporal_address)


async def run_blackbox_scan(input: BlackboxPipelineInput, temporal_address: str):
    """Run blackbox scan and return result."""
    from shannon_blackbox.worker import run_scan
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
    )

    wb_result = await run_whitebox_scan(wb_input, temporal_address)

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
    )

    bb_result = await run_blackbox_scan(bb_input, temporal_address)

    # Convert dataclass result to dict if needed
    if hasattr(bb_result, "__dataclass_fields__"):
        from dataclasses import asdict
        bb_dict = asdict(bb_result)
    else:
        bb_dict = bb_result if isinstance(bb_result, dict) else {"status": str(bb_result)}

    bb_dict["whitebox_workspace"] = workspace_name
    return bb_dict
