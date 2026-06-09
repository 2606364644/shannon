import asyncio
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from .pipeline.activities import (
    render_findings,
    run_agent,
    run_auth_validation,
    run_code_index,
    run_credential_check,
    run_preflight,
    run_rebuild_call_chains,
    run_risk_scoring,
    run_save_adjudication,
    run_vuln_agent,
)
from .pipeline.workflows import WhiteboxScanWorkflow
from .pipeline.shared import PipelineInput
from shannon_core.utils.paths import resolve_workspaces_dir
from shannon_core.services.temporal_infra import generate_task_queue

TASK_QUEUE_PREFIX = "shannon-py-wb"


async def poll_workflow_progress(handle, interval_seconds: int = 30) -> None:
    """Periodically query workflow progress and print status to console."""
    while True:
        try:
            progress = await handle.query("PipelineProgress")
            elapsed = int(progress.elapsed_ms / 1000)
            phase = progress.current_phase or "unknown"
            agent = progress.current_agent or "none"
            completed = len(progress.completed_agents)
            print(f"[{elapsed}s] Phase: {phase} | Agent: {agent} | Completed: {completed}/13")
        except Exception:
            pass  # Workflow may have completed
        await asyncio.sleep(interval_seconds)


async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233") -> dict:
    from shannon_core.session import SessionManager

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
            render_findings,
            run_agent,
            run_auth_validation,
            run_code_index,
            run_credential_check,
            run_preflight,
            run_rebuild_call_chains,
            run_risk_scoring,
            run_save_adjudication,
            run_vuln_agent,
        ],
    )

    async with worker:
        handle = await client.start_workflow(
            WhiteboxScanWorkflow.run,
            input,
            id=input.workspace_name or f"whitebox-{int(asyncio.get_event_loop().time())}",
            task_queue=task_queue,
        )
        poll_task = asyncio.create_task(poll_workflow_progress(handle))
        try:
            result = await handle.result()
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

            # Convert PipelineState to enriched dict for CLI consumption
            result_dict = asdict(result) if not isinstance(result, dict) else dict(result)
            result_dict["workspace_name"] = input.workspace_name
            result_dict["web_url"] = input.web_url

            workspaces_dir = resolve_workspaces_dir(input.repo_path)
            if input.workspace_name:
                result_dict["deliverables_path"] = str(
                    workspaces_dir / input.workspace_name / input.deliverables_subdir
                )
            else:
                result_dict["deliverables_path"] = str(
                    Path(input.repo_path) / input.deliverables_subdir
                )

            return result_dict
        except Exception:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            raise


def main():
    import sys
    asyncio.run(run_scan(PipelineInput(repo_path=sys.argv[1] if len(sys.argv) > 1 else ".")))
