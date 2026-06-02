import asyncio
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from .pipeline.activities import run_agent, run_code_index, run_preflight, run_vuln_agent, run_rebuild_call_chains
from .pipeline.workflows import WhiteboxScanWorkflow
from .pipeline.shared import PipelineInput

TASK_QUEUE = "shannon-whitebox"


async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233") -> dict:
    from shannon_core.session import SessionManager

    # Persist session data so blackbox can discover repo_path
    if input.workspace_name:
        workspaces_dir = Path(input.repo_path).parent / "workspaces"
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
