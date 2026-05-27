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
from .pipeline.shared import BlackboxPipelineInput

TASK_QUEUE = "shannon-blackbox"


async def run_scan(input: BlackboxPipelineInput, temporal_address: str = "localhost:7233") -> dict:
    client = await Client.connect(temporal_address)

    worker = Worker(
        client=client,
        task_queue=TASK_QUEUE,
        workflows=[BlackboxScanWorkflow],
        activities=[run_blackbox_preflight, run_recon, run_exploit_agent, assemble_report, run_report_agent],
    )

    async with worker:
        result = await client.execute_workflow(
            BlackboxScanWorkflow.run,
            input,
            id=input.workspace_name or f"blackbox-{int(asyncio.get_event_loop().time())}",
            task_queue=TASK_QUEUE,
        )
        return result


def main():
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"
    asyncio.run(run_scan(BlackboxPipelineInput(web_url=url)))
