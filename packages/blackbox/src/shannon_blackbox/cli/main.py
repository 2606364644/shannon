import asyncio
import json
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
from shannon_core.workspace import compute_deliverables_summary, find_latest_workspace, find_workspaces_by_url, get_workspace_vuln_counts, get_workspace_age_human


@click.group()
def cli():
    """Shannon Black-Box Scanner - Runtime vulnerability verification."""
    load_dotenv(override=True)


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
@click.option("--max-concurrent", default=3, type=int, help="Max concurrent exploit agents (default: 3)")
@click.option("--retry-profile", "retry_profile", default=None, type=click.Choice(["production", "testing", "subscription"]), help="Retry policy profile")
def start(url, repo, output, workspace, latest, config_path, vuln_classes, no_exploit, pipeline_testing, temporal_address, max_concurrent, retry_profile):
    """Start a black-box security scan."""
    from shannon_blackbox.worker import run_scan
    from shannon_blackbox.pipeline.shared import BlackboxPipelineInput

    selected = list(vuln_classes) if vuln_classes else list(ALL_VULN_CLASSES)

    # Warn on conflicting flags
    if latest and workspace:
        click.echo("⚠ Both --latest and -w specified; -w takes precedence.")

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
            click.echo("")
            for i, (ws_path, summary) in enumerate(matches, 1):
                counts = get_workspace_vuln_counts(ws_path)
                age = get_workspace_age_human(ws_path)
                counts_str = " ".join(f"{k}:{v}" for k, v in sorted(counts.items()))
                status_icon = "✅" if summary["vuln_queues"] else "⚠️"
                click.echo(f"  #{i}  {ws_path.name:<30} ({age:>6})   {counts_str:<25} {status_icon}")
            click.echo("")
            choice = click.prompt(
                "Select workspace [1-{}] or 'n' for standalone".format(len(matches)),
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
        max_concurrent=max_concurrent,
        retry_profile=retry_profile,
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
@click.option("--follow", is_flag=True, help="Tail the log in real-time (auto-exits on completion)")
def logs(workspace_name, follow):
    """View workspace execution logs."""
    workspaces_dir = Path("workspaces")
    ws = workspaces_dir / workspace_name
    if not ws.exists():
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)
    log_file = ws / "workflow.log"
    if not log_file.exists():
        click.echo("No logs found")
        return
    if follow:
        from shannon_core.cli.logs import tail_workflow_log
        tail_workflow_log(workspace_name)
    else:
        click.echo(log_file.read_text())


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
    source = result.get("source", "unknown")
    health_str = "healthy" if healthy else "not healthy"
    click.echo(f"Container: {container}")
    click.echo(f"Source:    {source}")
    click.echo(f"Health:    {health_str}")


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


@workspace.command()
@click.argument("workspace_name")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
def delete(workspace_name, force):
    """Delete a workspace and all its data."""
    mgr = SessionManager(Path("workspaces"))
    ws = mgr.get_workspace(workspace_name)
    if ws is None:
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)

    scan_type = mgr.get_scan_type(ws)
    status = mgr.get_status(ws)
    url = mgr.get_web_url(ws) or "unknown"
    links = mgr.get_links(ws)

    click.echo(f"Workspace to delete: {workspace_name}")
    click.echo(f"  Type:   {scan_type}")
    click.echo(f"  Target: {url}")
    click.echo(f"  Status: {status}")

    if status == "running":
        click.echo("  ⚠ This workspace appears to be running.")

    children = links.get("child_workspaces", [])
    if children:
        click.echo(f"  ⚠ Has {len(children)} child workspace(s)")

    parent = links.get("parent_workspace")
    if parent:
        click.echo(f"  ⚠ Child of: {parent}")

    if not force:
        if not click.confirm("Delete this workspace?", default=False):
            click.echo("Deletion cancelled.")
            return

    if mgr.delete_workspace(workspace_name):
        click.echo(f"✅ Workspace '{workspace_name}' deleted.")
    else:
        click.echo(f"❌ Failed to delete workspace '{workspace_name}'.")
        raise SystemExit(1)


def main():
    cli()
