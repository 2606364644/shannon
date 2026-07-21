import asyncio
import json
import time
from pathlib import Path

import click

from supernova_core.config.concurrency import get_max_concurrent
from supernova_core.config.env_loader import load_env
from supernova_core.config.profile_validator import validate_active_profile
from supernova_core.logging import configure_logging

from supernova_core.models.agents import ALL_VULN_CLASSES
from supernova_core.services.temporal_infra import (
    ensure_infra,
    get_temporal_status,
    is_temporal_ready,
    start_temporal,
    stop_temporal,
)
from supernova_core.session import SessionManager
from supernova_core.workspace import compute_deliverables_summary, find_latest_workspace


@click.group()
def cli():
    """Supernova Black-Box Scanner - Runtime vulnerability verification."""
    load_env()
    validate_active_profile()


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
@click.option(
    "--max-concurrent",
    default=get_max_concurrent,
    type=int,
    help="Max concurrent exploit agents (env: SUPERNOVA_MAX_CONCURRENT, default: 3)",
)
@click.option("--retry-profile", "retry_profile", default=None, type=click.Choice(["production", "testing", "subscription"]), help="Retry policy profile")
@click.option("--plain", is_flag=True, help="Disable Rich live dashboard; print one line per event (CI/pipes).")
@click.option("--rerun", is_flag=True, help="强制重跑黑盒（归档旧 evidence，基于已有白盒结果重新跑）")
@click.option("--correlated-workspace", default=None,
              help="Cross-repo correlation workspace (reuse topology for gateway-layer validation)")
@click.option("--debug", is_flag=True, help="扫描失败时在终端打印完整堆栈（调试用）")
def start(url, repo, output, workspace, latest, config_path, vuln_classes, no_exploit, pipeline_testing, temporal_address, max_concurrent, retry_profile, plain, rerun, correlated_workspace, debug):
    """Start a black-box security scan."""
    from supernova_blackbox.worker import run_scan
    from supernova_blackbox.pipeline.shared import BlackboxPipelineInput

    selected = list(vuln_classes) if vuln_classes else list(ALL_VULN_CLASSES)

    # Warn on conflicting flags
    if latest and workspace:
        click.echo("⚠ Both --latest and -w specified; -w takes precedence.")

    # Workspace 发现逻辑（spec 决策 4）：
    #   -w 优先 → 显式指定
    #   否则默认 --latest（软默认）：复用最近白盒 workspace 的 deliverables
    #     找到 → 接上；找不到：
    #       显式 --latest → 报错退出（用户明确要复用却没结果）
    #       软默认（无 flag）→ standalone，不报错（worker 自建 blackbox session）
    resolved_workspace = workspace
    if not workspace:
        wb_ws = find_latest_workspace(Path("workspaces"), scan_type="whitebox", url=url)
        if wb_ws is not None:
            summary = compute_deliverables_summary(wb_ws)
            if summary["vuln_queues"]:
                resolved_workspace = wb_ws.name
                queues = ", ".join(summary["vuln_queues"])
                click.echo(f"Found white-box results in workspace '{wb_ws.name}'")
                click.echo(f"   Vulnerability queues found: {queues}")
                click.echo("   Skipping recon phase — leveraging white-box findings directly.")
            elif latest:
                # 显式 --latest 但最近 workspace 无 deliverables → 报错
                click.echo("Latest workspace has no deliverables. Specify a workspace with -w.")
                raise SystemExit(1)
            # 软默认无 deliverables → 静默 standalone
        elif latest:
            # 显式 --latest 但无任何白盒 workspace → 报错
            click.echo("No white-box workspaces found. Run a white-box scan first.")
            raise SystemExit(1)

    repo_path_resolved = str(Path(repo).resolve()) if repo else None
    # workspaces 根在 sandbox 外解析（workflow sandbox 禁 os.getenv/Path.cwd），经 input 传入
    from supernova_core.utils.paths import resolve_workspaces_dir
    input = BlackboxPipelineInput(
        web_url=url,
        repo_path=repo_path_resolved,
        workspace_name=resolved_workspace,
        config_path=config_path,
        output_path=str(Path(output).resolve()) if output else None,
        vuln_classes=selected,
        exploit=not no_exploit,
        pipeline_testing_mode=pipeline_testing,
        max_concurrent=max_concurrent,
        retry_profile=retry_profile,
        correlated_workspace=correlated_workspace,
        workspaces_root=str(resolve_workspaces_dir(repo_path_resolved)),
    )

    # 幂等检测：默认（非 --rerun）若已跑过黑盒 → 告知、不启动 worker（省 Temporal 连接）
    # 仅在能定位 deliverables 时检测（有 repo 或 workspace）；standalone 模式跳过。
    if not rerun and (repo or resolved_workspace):
        from supernova_core.utils.paths import resolve_deliverables_path
        from supernova_blackbox.pipeline.blackbox_rerun import detect_blackbox_completed
        deliverables = resolve_deliverables_path(
            repo_path=str(Path(repo).resolve()) if repo else None,
            deliverables_subdir=input.deliverables_subdir,
            workspace_name=resolved_workspace,
        )
        if detect_blackbox_completed(deliverables):
            click.echo(
                f"该 workspace 已跑过黑盒，结果在 {deliverables}。"
                f"如需重跑请加 --rerun（旧 evidence 会归档到 .blackbox-archive/）。"
            )
            return
    input.rerun = rerun

    click.echo(f"Starting black-box scan on {url}")
    asyncio.run(ensure_infra(address=temporal_address))
    from supernova_core.runtime.prerequisites import ensure_browser_engine
    ensure_browser_engine(input.config_path, profile="blackbox")
    # spec 组件 5：统一日志入口（诊断日志落 workspaces/<session>/logs/diagnostic.log）。
    configure_logging(
        log_dir=Path(input.workspaces_root) / (input.workspace_name or "default") / "logs"
    )
    import sys
    use_rich = sys.stdout.isatty() and not plain
    try:
        result = asyncio.run(run_scan(input, temporal_address, use_rich=use_rich))
    except Exception as e:
        from supernova_core.cli.error_render import format_workflow_failure, persist_workflow_traceback
        workspace_dir = None
        if input.workspaces_root and input.workspace_name:
            workspace_dir = Path(input.workspaces_root) / input.workspace_name
        log_path = persist_workflow_traceback(e, workspace_dir)
        click.echo(format_workflow_failure(e))
        if log_path:
            click.echo(f"  完整错误已记录到 {log_path}")
        click.echo("  加 --debug 可在终端查看完整堆栈。")
        if debug:
            import traceback as _tb
            _tb.print_exc()
        raise SystemExit(1)
    if result.status == "cancelled":
        click.echo("Scan cancelled.")
        raise SystemExit(130)
    elif result.status == "completed":
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
@click.option(
    "--diagnostic", is_flag=True,
    help="Read diagnostic.log (logging WARNING/ERROR) instead of workflow.log",
)
def logs(workspace_name, follow, diagnostic):
    """View workspace execution logs."""
    workspaces_dir = Path("workspaces")
    ws = workspaces_dir / workspace_name
    if not ws.exists():
        click.echo(f"Workspace not found: {workspace_name}")
        raise SystemExit(1)
    # spec 组件 6：--diagnostic 读 logs/diagnostic.log，否则 workflow.log。
    log_filename = "diagnostic.log" if diagnostic else "workflow.log"
    log_file = ws / ("logs" if diagnostic else "") / log_filename
    if not log_file.exists():
        click.echo("No logs found")
        return
    if follow:
        from supernova_core.cli.logs import tail_workflow_log
        tail_workflow_log(workspace_name, log_filename=log_filename)
    else:
        click.echo(log_file.read_text())


@cli.command()
def workspaces():
    """List all workspaces grouped by scan type."""
    from supernova_core.workspace import compute_deliverables_summary

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
    from supernova_core.workspace import compute_deliverables_summary, get_workspace_info

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
        from supernova_core.utils.paths import (
            deliverables_dir_for_workspace,
            resolve_track_deliverable,
            WHITEBOX_SUBDIR,
        )
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
        click.echo(f"    supernova-blackbox start --url {url} -w {info['name']}")


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
