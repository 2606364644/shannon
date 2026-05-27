import asyncio
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from .pipeline.activities import run_agent, run_preflight, run_vuln_agent
from .pipeline.workflows import WhiteboxScanWorkflow
from .pipeline.shared import PipelineInput

TASK_QUEUE = "shannon-whitebox"

async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233") -> dict:
    client = await Client.connect(temporal_address)

    worker = Worker(
        client=client,
        task_queue=TASK_QUEUE,
        workflows=[WhiteboxScanWorkflow],
        activities=[run_preflight, run_agent, run_vuln_agent],
    )

    async with worker:
        result = await client.execute_workflow(
            WhiteboxScanWorkflow.run,
            input,
            id=input.workspace_name or f"whitebox-{int(asyncio.get_event_loop().time())}",
            task_queue=TASK_QUEUE,
        )
        return result

def main():
    import sys
    asyncio.run(run_scan(PipelineInput(repo_path=sys.argv[1] if len(sys.argv) > 1 else ".")))
