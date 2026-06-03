import asyncio
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
    """List all workspaces."""
    mgr = SessionManager(Path("workspaces"))
    for ws in mgr.list_workspaces():
        data = mgr.get_session_data(ws)
        url = data.get("web_url", "unknown")
        agents = len(data.get("completed_agents", []))
        click.echo(f"  {ws.name}  url={url}  agents={agents}")


def main():
    cli()
