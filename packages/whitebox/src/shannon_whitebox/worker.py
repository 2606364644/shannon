import asyncio
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from .pipeline.activities import run_agent, run_code_index, run_preflight, run_vuln_agent, run_rebuild_call_chains
from .pipeline.workflows import WhiteboxScanWorkflow
from .pipeline.shared import PipelineInput
from shannon_core.utils.paths import resolve_workspaces_dir

TASK_QUEUE = "shannon-whitebox"


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

    worker = Worker(
        client=client,
        task_queue=TASK_QUEUE,
        workflows=[WhiteboxScanWorkflow],
        activities=[run_preflight, run_agent, run_vuln_agent, run_code_index, run_rebuild_call_chains],
    )

    async with worker:
        handle = await client.start_workflow(
            WhiteboxScanWorkflow.run,
            input,
            id=input.workspace_name or f"whitebox-{int(asyncio.get_event_loop().time())}",
            task_queue=TASK_QUEUE,
        )
        poll_task = asyncio.create_task(poll_workflow_progress(handle))
        try:
            result = await handle.result()
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            return result
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
