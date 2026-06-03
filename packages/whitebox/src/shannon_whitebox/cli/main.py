import asyncio
import json
import time

import click
from pathlib import Path

from dotenv import load_dotenv

from shannon_core.services.temporal_infra import (
    ensure_infra,
    get_temporal_status,
    is_temporal_ready,
    start_temporal,
    stop_temporal,
)
from shannon_core.session import SessionManager
from shannon_whitebox.pipeline.shared import PipelineInput


@click.group()
def cli():
    """Shannon White-Box Scanner - Source code vulnerability analysis."""
    load_dotenv()


@cli.command()
@click.option("-r", "--repo", required=True, help="Target repository path")
@click.option("-o", "--output", default=None, help="Output directory for deliverables")
@click.option("-w", "--workspace", default=None, help="Workspace name (supports resume)")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def start(repo, output, workspace, config_path, pipeline_testing, temporal_address):
    """Start a white-box security scan."""
    from shannon_whitebox.worker import run_scan

    input = PipelineInput(
        repo_path=str(Path(repo).resolve()),
        output_path=str(Path(output).resolve()) if output else None,
        workspace_name=workspace,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
    )
    click.echo(f"Starting white-box scan on {repo}")
    asyncio.run(ensure_infra(address=temporal_address))
    result = asyncio.run(run_scan(input, temporal_address))
    if result.get("status") == "completed":
        ws_name = result.get("workspace_name", "unknown")
        deliverables_path = result.get("deliverables_path", "")
        web_url = result.get("web_url", "<target-url>")

        click.echo("")
        click.echo("White-box scan complete.")
        click.echo("")
        click.echo(f"  Workspace:     {ws_name}")
        if deliverables_path:
            click.echo(f"  Deliverables:  {deliverables_path}")
        click.echo("")
        click.echo("  Next steps:")
        click.echo(f"    shannon-blackbox start --url {web_url} -w {ws_name}")
        click.echo("    # or use --latest to reuse the most recent white-box results:")
        click.echo(f"    shannon-blackbox start --url {web_url} --latest")
    else:
        click.echo(f"Scan failed: {result.get('error', 'unknown error')}")
        raise SystemExit(1)


@cli.group()
def infra():
    """Manage Temporal infrastructure."""


@infra.command()
def up():
    """Start Temporal server."""
    start_temporal()
    click.echo("Waiting for Temporal to be ready...")
    for _ in range(30):
        if asyncio.run(is_temporal_ready()):
            click.echo("Temporal is ready!")
            return
        time.sleep(2)
    click.echo("Warning: Temporal may not be ready yet. Check `docker compose logs`.")


@infra.command()
def down():
    """Stop Temporal server."""
    stop_temporal()
    click.echo("Temporal stopped.")


@infra.command()
def status():
    """Check Temporal server status."""
    result = asyncio.run(get_temporal_status())
    container = result.get("container", "unknown")
    healthy = result.get("healthy", False)
    health_str = "healthy" if healthy else "not healthy"
    click.echo(f"Container: {container}")
    click.echo(f"Health: {health_str}")


@cli.command()
@click.argument("workspace_name")
def logs(workspace_name):
    """View workspace execution logs."""
    workspaces_dir = Path("workspaces")
    ws = workspaces_dir / workspace_name
    if not ws.exists():
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)
    log_file = ws / "workflow.log"
    if log_file.exists():
        click.echo(log_file.read_text())
    else:
        click.echo("No logs found")


@cli.command()
def workspaces():
    """List all workspaces grouped by scan type."""
    from shannon_core.workspace import compute_deliverables_summary

    mgr = SessionManager(Path("workspaces"))
    all_ws = mgr.list_workspaces()

    whitebox = []
    blackbox = []
    for ws in all_ws:
        info = {
            "name": ws.name,
            "url": mgr.get_web_url(ws) or "unknown",
            "status": mgr.get_status(ws),
            "scan_type": mgr.get_scan_type(ws),
            "summary": compute_deliverables_summary(ws),
            "links": mgr.get_links(ws),
        }
        if info["scan_type"] == "blackbox":
            blackbox.append(info)
        else:
            whitebox.append(info)

    if whitebox:
        click.echo("")
        click.echo("White-box workspaces:")
        click.echo(f"  {'NAME':<30} {'TARGET':<25} {'STATUS':<12} {'VULN QUEUES':<20}")
        for info in whitebox:
            queues = ", ".join(info["summary"]["vuln_queues"]) or "-"
            click.echo(f"  {info['name']:<30} {info['url']:<25} {info['status']:<12} {queues:<20}")

    if blackbox:
        click.echo("")
        click.echo("Black-box workspaces:")
        click.echo(f"  {'NAME':<30} {'TARGET':<25} {'STATUS':<12} {'PARENT WORKSPACE':<30}")
        for info in blackbox:
            parent = info["links"].get("parent_workspace") or "-"
            click.echo(f"  {info['name']:<30} {info['url']:<25} {info['status']:<12} {parent:<30}")

    if not whitebox and not blackbox:
        click.echo("No workspaces found.")


@cli.group()
def workspace():
    """Workspace management commands."""


@workspace.command()
@click.argument("workspace_name")
def show(workspace_name):
    """Show detailed workspace information."""
    from shannon_core.workspace import compute_deliverables_summary, get_workspace_info

    mgr = SessionManager(Path("workspaces"))
    ws = mgr.get_workspace(workspace_name)
    if ws is None:
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)

    info = get_workspace_info(ws)

    click.echo(f"\nWorkspace: {info['name']}")
    click.echo(f"  Type:           {info['scan_type']}")
    click.echo(f"  Target:         {info['web_url'] or 'unknown'}")
    click.echo(f"  Repo:           {info['repo_path'] or 'unknown'}")
    click.echo(f"  Status:         {info['status']}")

    created = info["created_at"]
    completed = info["completed_at"]
    click.echo(f"  Created:        {created or 'unknown'}")
    click.echo(f"  Completed:      {completed or 'N/A'}")

    # Duration
    if created and completed:
        try:
            c_time = float(created)
            e_time = float(completed)
            duration_secs = int(e_time - c_time)
            hours, remainder = divmod(duration_secs, 3600)
            minutes, secs = divmod(remainder, 60)
            click.echo(f"  Duration:       {hours}h {minutes}m {secs}s")
        except (ValueError, TypeError):
            pass

    # Deliverables
    summary = info["deliverables_summary"]
    if summary["vuln_queues"] or summary["reports"]:
        click.echo("\n  Deliverables:")
        deliverables_dir = ws / "deliverables"
        for vc in summary["vuln_queues"]:
            filename = f"{vc}_exploitation_queue.json"
            filepath = deliverables_dir / filename
            if filepath.exists():
                try:
                    data = json.loads(filepath.read_text(encoding="utf-8"))
                    count = len(data.get("vulnerabilities", []))
                    click.echo(f"    OK {filename}  ({count} findings)")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    click.echo(f"    WARN {filename}  (invalid)")
            else:
                click.echo(f"    OK {filename}")

        for report in summary["reports"]:
            click.echo(f"    OK {report}")

    # Links
    links = info["links"]
    children = links.get("child_workspaces", [])
    if children:
        click.echo("\n  Linked black-box scans:")
        for child in children:
            child_ws = mgr.get_workspace(child)
            if child_ws:
                child_status = mgr.get_status(child_ws)
                click.echo(f"    - {child} ({child_status})")
            else:
                click.echo(f"    - {child}")

    parent = links.get("parent_workspace")
    if parent:
        click.echo(f"\n  Parent workspace: {parent}")

    # Reuse command
    url = info["web_url"]
    if url and info["scan_type"] == "whitebox":
        click.echo(f"\n  Reuse command:")
        click.echo(f"    shannon-blackbox start --url {url} -w {info['name']}")


def main():
    cli()
