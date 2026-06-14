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
    run_merge_sink_reports,
    run_entry_point_fusion,
    run_preflight,
    run_render_dataflow_hints,
    run_risk_scoring,
    run_save_adjudication,
    run_vuln_agent,
    run_attack_chain_assembly,
    run_framework_analysis,
    run_frontend_mapping,
    run_route_chain_building,
    log_phase_start_activity,
    log_phase_complete_activity,
)
from .pipeline.workflows import WhiteboxScanWorkflow
from .pipeline.shared import PipelineInput
from shannon_core.utils.paths import resolve_workspaces_dir
from shannon_core.services.temporal_infra import generate_task_queue

TASK_QUEUE_PREFIX = "shannon-py-wb"


async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233",
                   use_rich: bool = False) -> dict:
    from rich.console import Console
    from shannon_core.session import SessionManager
    from shannon_core.models.metrics import SessionMetadata
    from shannon_whitebox.audit.display_lifecycle import run_with_display

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
            render_findings, run_agent, run_auth_validation, run_code_index,
            run_credential_check, run_merge_sink_reports, run_entry_point_fusion,
            run_preflight, run_render_dataflow_hints, run_risk_scoring,
            run_save_adjudication, run_vuln_agent, run_attack_chain_assembly,
            run_framework_analysis, run_frontend_mapping, run_route_chain_building,
            log_phase_start_activity, log_phase_complete_activity,
        ],
    )

    meta = SessionMetadata(
        id=input.workspace_name or "whitebox-scan",
        web_url=input.web_url,
        repo_path=input.repo_path,
        output_path=str(resolve_workspaces_dir(input.repo_path)),
    )

    async with worker:
        async with run_with_display(meta, use_rich=use_rich) as session:
            from shannon_whitebox.audit.session_registry import (
                set_audit_session, clear_audit_session,
            )
            set_audit_session(session)
            handle = await client.start_workflow(
                WhiteboxScanWorkflow.run,
                input,
                id=input.workspace_name or f"whitebox-{int(asyncio.get_event_loop().time())}",
                task_queue=task_queue,
            )
            try:
                result = await handle.result()
            finally:
                clear_audit_session()

            result_dict = asdict(result) if not isinstance(result, dict) else dict(result)
            result_dict["workspace_name"] = input.workspace_name
            result_dict["web_url"] = input.web_url

            workspaces_dir = resolve_workspaces_dir(input.repo_path)
            if input.workspace_name:
                result_dict["deliverables_path"] = str(
                    workspaces_dir / input.workspace_name / input.deliverables_subdir)
            else:
                result_dict["deliverables_path"] = str(
                    Path(input.repo_path) / input.deliverables_subdir)
            return result_dict


def main():
    import sys
    asyncio.run(run_scan(PipelineInput(repo_path=sys.argv[1] if len(sys.argv) > 1 else ".")))
