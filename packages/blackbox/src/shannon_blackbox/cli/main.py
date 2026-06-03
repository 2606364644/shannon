import asyncio
import time
from pathlib import Path

import click

from dotenv import load_dotenv

from shannon_core.models.agents import ALL_VULN_CLASSES
from shannon_core.services.temporal_infra import (
    ensure_infra,
    get_temporal_status,
    is_temporal_ready,
    start_temporal,
    stop_temporal,
)
from shannon_core.session import SessionManager
from shannon_core.workspace import compute_deliverables_summary, find_latest_workspace, find_workspaces_by_url


@click.group()
def cli():
    """Shannon Black-Box Scanner - Runtime vulnerability verification."""
    load_dotenv()


@cli.command()
@click.option("--url", required=True, help="Target URL to scan")
@click.option("-r", "--repo", default=None, help="Target repository path (to reuse whitebox results)")
@click.option("-o", "--output", default=None, help="Output directory for deliverables")
@click.option("-w", "--workspace", default=None, help="Workspace name (resume if exists)")
@click.option("--latest", is_flag=True, help="Reuse the most recent white-box workspace deliverables")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--vuln-classes", multiple=True, help="Vuln classes to test (default: all)")
@click.option("--no-exploit", is_flag=True, help="Skip exploitation phase")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def start(url, repo, output, workspace, latest, config_path, vuln_classes, no_exploit, pipeline_testing, temporal_address):
    """Start a black-box security scan."""
    from shannon_blackbox.worker import run_scan
    from shannon_blackbox.pipeline.shared import BlackboxPipelineInput

    selected = list(vuln_classes) if vuln_classes else list(ALL_VULN_CLASSES)

    # Resolve --latest: find most recent whitebox workspace with deliverables
    resolved_workspace = workspace
    if latest and not workspace:
        wb_ws = find_latest_workspace(Path("workspaces"), scan_type="whitebox", url=url)
        if wb_ws is None:
            click.echo("No white-box workspaces found. Run a white-box scan first.")
            raise SystemExit(1)
        summary = compute_deliverables_summary(wb_ws)
        if not summary["vuln_queues"]:
            click.echo("Latest workspace has no deliverables. Specify a workspace with -w.")
            raise SystemExit(1)
        resolved_workspace = wb_ws.name
        queues = ", ".join(summary["vuln_queues"])
        click.echo(f"Found white-box results in workspace '{wb_ws.name}'")
        click.echo(f"   Vulnerability queues found: {queues}")
        click.echo("   Skipping recon phase — leveraging white-box findings directly.")

    elif not workspace and not latest:
        # Auto-detect: find whitebox workspaces for the same target URL
        matches = find_workspaces_by_url(Path("workspaces"), url, scan_type="whitebox")

        if len(matches) == 1:
            ws_path, summary = matches[0]
            click.echo(f"Detected white-box results for '{url}' (workspace: {ws_path.name})")
            if click.confirm("   Reuse these results?", default=True):
                resolved_workspace = ws_path.name
                queues = ", ".join(summary["vuln_queues"])
                click.echo(f"   Using workspace '{ws_path.name}' ({queues})")
            else:
                click.echo("Running standalone black-box scan.")

        elif len(matches) > 1:
            click.echo(f"Found {len(matches)} white-box workspaces for '{url}':")
            for i, (ws_path, summary) in enumerate(matches, 1):
                queues = ", ".join(summary["vuln_queues"])
                click.echo(f"  [{i}] {ws_path.name}  ({queues})")
            click.echo("")
            choice = click.prompt(
                "Select workspace to reuse [1-{}] or 'n' for standalone".format(len(matches)),
                default="1",
            )
            if choice.strip().lower() == "n":
                click.echo("Running standalone black-box scan.")
            else:
                try:
                    idx = int(choice.strip()) - 1
                    if 0 <= idx < len(matches):
                        resolved_workspace = matches[idx][0].name
                        click.echo(f"   Using workspace '{resolved_workspace}'")
                    else:
                        click.echo("Invalid selection. Running standalone.")
                except ValueError:
                    click.echo("Invalid selection. Running standalone.")

        else:
            click.echo("No white-box results found for this target. Running standalone black-box scan.")
            click.echo("   Tip: run white-box first, then use --latest to reuse results.")

    input = BlackboxPipelineInput(
        web_url=url,
        repo_path=str(Path(repo).resolve()) if repo else None,
        workspace_name=resolved_workspace,
        config_path=config_path,
        output_path=str(Path(output).resolve()) if output else None,
        vuln_classes=selected,
        exploit=not no_exploit,
        pipeline_testing_mode=pipeline_testing,
    )
    click.echo(f"Starting black-box scan on {url}")
    asyncio.run(ensure_infra(address=temporal_address))
    result = asyncio.run(run_scan(input, temporal_address))
    if result.status == "completed":
        if result.has_whitebox_results:
            classes = result.found_whitebox_classes
            click.echo(f"Scan completed (leveraged whitebox results for: {', '.join(classes)})")
        else:
            click.echo("Scan completed (standalone — no whitebox results found)")
    else:
        error_msg = result.errors[-1] if result.errors else "unknown error"
        click.echo(f"Scan failed: {error_msg}")
        raise SystemExit(1)


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


def main():
    cli()
