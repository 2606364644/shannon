"""Supernova Combined CLI — unified whitebox→blackbox scan."""

import asyncio

import click
from supernova_core.config.env_loader import load_env
from supernova_core.config.profile_validator import validate_active_profile
from supernova_core.logging import configure_logging
from supernova_core.services.temporal_infra import ensure_infra
from supernova_core.utils.paths import resolve_workspaces_dir


@click.group()
def cli():
    """Supernova — unified security scanning (whitebox + blackbox)."""
    load_env()
    validate_active_profile()


@cli.command()
@click.option("--repo", "-r", required=True, help="Target repository path")
@click.option("--url", "-u", required=True, help="Target URL for blackbox verification")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def scan(repo, url, config_path, pipeline_testing, temporal_address):
    """Run whitebox scan followed by blackbox verification."""
    from pathlib import Path

    from supernova_combined.orchestrator import run_combined_scan

    repo_path = str(Path(repo).resolve())
    click.echo(f"Starting combined scan: whitebox → blackbox")
    click.echo(f"  Repository: {repo_path}")
    click.echo(f"  Target URL: {url}")

    asyncio.run(ensure_infra(address=temporal_address))
    # spec 组件 5：统一日志入口（诊断日志落 workspaces/logs/diagnostic.log）。
    configure_logging(log_dir=resolve_workspaces_dir(repo_path) / "logs")
    result = asyncio.run(run_combined_scan(
        repo_path=repo_path,
        url=url,
        temporal_address=temporal_address,
        config_path=config_path,
        pipeline_testing=pipeline_testing,
    ))

    if result.get("status") == "cancelled":
        click.echo("Scan cancelled.")
        raise SystemExit(130)
    elif result.get("status") == "completed":
        wb_ws = result.get("whitebox_workspace", "unknown")
        classes = result.get("found_whitebox_classes", [])
        if classes:
            click.echo(f"\n✅ Combined scan completed!")
            click.echo(f"  Whitebox workspace: {wb_ws}")
            click.echo(f"  Verified classes: {', '.join(classes)}")
        else:
            click.echo(f"\n✅ Combined scan completed (no whitebox results leveraged)")
    else:
        phase = result.get("phase", "unknown")
        error = result.get("error", "unknown error")
        click.echo(f"\n❌ Combined scan failed during {phase}: {error}")
        raise SystemExit(1)


def main():
    cli()
