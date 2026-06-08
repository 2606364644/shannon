import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from .pipeline.activities import (
    run_blackbox_preflight,
    run_recon,
    run_exploit_agent,
    assemble_report,
    run_report_agent,
)
from .pipeline.workflows import BlackboxScanWorkflow
from .pipeline.shared import BlackboxPipelineInput, BlackboxPipelineState
from shannon_core.services.temporal_infra import generate_task_queue

TASK_QUEUE_PREFIX = "shannon-py-bb"


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


async def run_scan(input: BlackboxPipelineInput, temporal_address: str = "localhost:7233") -> BlackboxPipelineState:
    client = await Client.connect(temporal_address)

    task_queue = generate_task_queue(TASK_QUEUE_PREFIX)

    worker = Worker(
        client=client,
        task_queue=task_queue,
        workflows=[BlackboxScanWorkflow],
        activities=[run_blackbox_preflight, run_recon, run_exploit_agent, assemble_report, run_report_agent],
    )

    async with worker:
        handle = await client.start_workflow(
            BlackboxScanWorkflow.run,
            input,
            id=input.workspace_name or f"blackbox-{int(asyncio.get_event_loop().time())}",
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
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"
    asyncio.run(run_scan(BlackboxPipelineInput(web_url=url)))
