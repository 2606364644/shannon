import asyncio
import json
import os
import time

import click
from pathlib import Path

from shannon_core.config.env_loader import load_env
from shannon_core.config.profile_validator import validate_active_profile
from shannon_core.config.concurrency import get_max_concurrent, is_llm_track_enabled
from shannon_core.config.vuln_selection import resolve_vuln_classes, InvalidVulnClass
from shannon_core.logging import configure_logging

from shannon_core.services.temporal_infra import (
    ensure_infra,
    get_temporal_status,
    is_temporal_ready,
    start_temporal,
    stop_temporal,
)
from shannon_core.session import SessionManager
from shannon_core.utils.paths import resolve_workspaces_dir, resolve_track_deliverable, WHITEBOX_SUBDIR
from shannon_whitebox.pipeline.shared import PipelineInput


@click.group()
def cli():
    """Shannon White-Box Scanner - Source code vulnerability analysis."""
    load_env()
    validate_active_profile()


@cli.command()
@click.option("-r", "--repo", required=True, help="Target repository path")
@click.option("-o", "--output", default=None, help="Output directory for deliverables")
@click.option("-w", "--workspace", default=None, help="Workspace name (supports resume)")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
@click.option("--plain", is_flag=True, help="Disable Rich live dashboard; print one line per event (CI/pipes).")
@click.option("--url", default=None, help="Deployed target URL (optional; recorded so blackbox can auto-detect this scan by URL)")
@click.option("--fresh", is_flag=True, help="全新扫描，忽略已有进度")
@click.option("--rewind", "rewind", default=None,
              type=click.Choice(["pre-recon", "recon", "vuln"]),
              help="回退到指定阶段重跑（pre-recon/recon/vuln）")
@click.option("--debug", is_flag=True, help="扫描失败时在终端打印完整堆栈（调试用）")
@click.option(
    "--vuln-classes", "vuln_classes_cli", default=None,
    help="逗号分隔的 vuln 类（如 injection,xss）；优先于 SHANNON_VULN_CLASSES env 与 YAML vuln_classes。"
)
def start(repo, output, workspace, config_path, pipeline_testing, temporal_address, plain, url, fresh, rewind, debug, vuln_classes_cli):
    """Start a white-box security scan."""
    if fresh and rewind:
        raise click.UsageError("--fresh 与 --rewind 互斥，不能同时使用。")

    # vuln 类优先级链: CLI > env（YAML/默认在 workflow 层 select_vuln_classes 兜底）。
    # env 在 CLI 层读（workflow sandbox 不变量：workflow.run() 内禁 env 解析）。
    try:
        override = resolve_vuln_classes(
            vuln_classes_cli,
            os.environ.get("SHANNON_VULN_CLASSES"),
        )
    except InvalidVulnClass as e:
        raise click.UsageError(str(e)) from e

    from shannon_whitebox.worker import run_scan

    input = PipelineInput(
        repo_path=str(Path(repo).resolve()),
        web_url=url or "",
        output_path=str(Path(output).resolve()) if output else None,
        workspace_name=workspace,
        config_path=config_path,
        pipeline_testing_mode=pipeline_testing,
        max_concurrent=get_max_concurrent(),
        enable_llm_track=is_llm_track_enabled(),
        vuln_classes=override,
    )
    if fresh:
        setattr(input, "_fresh", True)
    if rewind:
        setattr(input, "_rewind_target", rewind)
    click.echo(f"Starting white-box scan on {repo}")
    asyncio.run(ensure_infra(address=temporal_address))
    from shannon_core.runtime.prerequisites import ensure_prerequisite
    ensure_prerequisite("gitnexus", profile="whitebox")
    import sys
    use_rich = sys.stdout.isatty() and not plain
    from shannon_core.utils.paths import resolve_workspaces_dir
    # spec 组件 5：统一日志入口。诊断日志落 workspaces/<session>/logs/diagnostic.log，
    # 散落 getLogger 自动套 dictConfig 格式（A 档：不动调用点）。workspace_name 未指定
    # （worker 才生成 session id）时落 "default"，worker 可重新 configure 覆盖。
    configure_logging(
        log_dir=resolve_workspaces_dir(input.repo_path)
        / (input.workspace_name or "default") / "logs"
    )
    try:
        result = asyncio.run(run_scan(input, temporal_address, use_rich=use_rich))
    except Exception as e:
        from shannon_core.cli.error_render import format_workflow_failure, persist_workflow_traceback
        workspace_dir = None
        if input.workspace_name:
            workspace_dir = Path(resolve_workspaces_dir(input.repo_path)) / input.workspace_name
        log_path = persist_workflow_traceback(e, workspace_dir)
        click.echo(format_workflow_failure(e))
        if log_path:
            click.echo(f"  完整错误已记录到 {log_path}")
        click.echo("  加 --debug 可在终端查看完整堆栈。")
        if debug:
            import traceback as _tb
            _tb.print_exc()
        raise SystemExit(1)
    if result.get("status") == "cancelled":
        click.echo("Scan cancelled.")
        raise SystemExit(130)
    elif result.get("status") == "completed":
        ws_name = result.get("workspace_name", "unknown")
        deliverables_path = result.get("deliverables_path", "")
        web_url = result.get("web_url") or "<target-url>"

        click.echo("")
        click.echo("White-box scan complete.")
        click.echo("")

        # Results summary
        if deliverables_path:
            from shannon_core.workspace import summarize_deliverables_dir

            summary_path = Path(deliverables_path)
            if summary_path.exists():
                summary = summarize_deliverables_dir(summary_path)
                if summary["vuln_queues"]:
                    click.echo("Results summary:")
                    for vc in sorted(summary["vuln_queues"]):
                        queue_file = resolve_track_deliverable(summary_path, WHITEBOX_SUBDIR, f"{vc}_exploitation_queue.json")
                        try:
                            data = json.loads(queue_file.read_text(encoding="utf-8"))
                            count = len(data.get("vulnerabilities", []))
                        except (json.JSONDecodeError, OSError):
                            count = 0
                        click.echo(f"  ├─ {vc:<12} {count} vulnerabilities found")
                    click.echo("")

        click.echo(f"  Workspace:     {ws_name}")
        if deliverables_path:
            click.echo(f"  Deliverables:  {deliverables_path}")
        click.echo("")
        click.echo("  Next steps:")
        click.echo(f"    shannon-blackbox start --url {web_url} --repo {str(Path(repo).resolve())} -w {ws_name}")
        click.echo("    # or use --latest to reuse the most recent white-box results:")
        click.echo(f"    shannon-blackbox start --url {web_url} --repo {str(Path(repo).resolve())} --latest")
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
    source = result.get("source", "unknown")
    health_str = "healthy" if healthy else "not healthy"
    click.echo(f"Container: {container}")
    click.echo(f"Source:    {source}")
    click.echo(f"Health:    {health_str}")


@cli.command()
@click.argument("workspace_name")
@click.option("--follow", is_flag=True, help="Tail the log in real-time (auto-exits on completion)")
@click.option(
    "--diagnostic", is_flag=True,
    help="Read diagnostic.log (logging WARNING/ERROR) instead of workflow.log",
)
@click.option(
    "--full", is_flag=True,
    help="Read events.ndjson (full stream incl. LogEvent diagnostic lines)",
)
def logs(workspace_name, follow, diagnostic, full):
    """View workspace execution logs."""
    import json as _json
    workspaces_dir = resolve_workspaces_dir()
    ws = workspaces_dir / workspace_name
    if not ws.exists():
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)
    if full:
        events_file = ws / "events.ndjson"
        if not events_file.exists():
            click.echo("No events.ndjson found")
            return
        if follow:
            from shannon_core.cli.logs import tail_events_ndjson
            tail_events_ndjson(workspace_name)
        else:
            from shannon_core.cli.logs import render_event_line
            for line in events_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    click.echo(render_event_line(_json.loads(line)))
                except _json.JSONDecodeError:
                    continue
        return
    # spec 组件 6：--diagnostic 读 logs/diagnostic.log，否则 workflow.log（display 流产物）。
    log_filename = "diagnostic.log" if diagnostic else "workflow.log"
    log_file = ws / ("logs" if diagnostic else "") / log_filename
    if not log_file.exists():
        click.echo("No logs found")
        return
    if follow:
        from shannon_core.cli.logs import tail_workflow_log
        tail_workflow_log(workspace_name, log_filename=log_filename)
    else:
        click.echo(log_file.read_text())


@cli.command()
def workspaces():
    """List all workspaces grouped by scan type."""
    from shannon_core.workspace import compute_deliverables_summary

    mgr = SessionManager(resolve_workspaces_dir())
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

    mgr = SessionManager(resolve_workspaces_dir())
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
        from shannon_core.utils.paths import deliverables_dir_for_workspace
        deliverables_dir = deliverables_dir_for_workspace(ws)
        for vc in summary["vuln_queues"]:
            filename = f"{vc}_exploitation_queue.json"
            filepath = resolve_track_deliverable(deliverables_dir, WHITEBOX_SUBDIR, filename)
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
    mgr = SessionManager(resolve_workspaces_dir())
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
