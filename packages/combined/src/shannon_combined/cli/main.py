"""Shannon Combined CLI — unified whitebox→blackbox scan."""

import asyncio

import click
from dotenv import load_dotenv

from shannon_core.services.temporal_infra import ensure_infra


@click.group()
def cli():
    """Shannon — unified security scanning (whitebox + blackbox)."""
    load_dotenv(override=True)


@cli.command()
@click.option("--repo", "-r", required=True, help="Target repository path")
@click.option("--url", "-u", required=True, help="Target URL for blackbox verification")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def scan(repo, url, config_path, pipeline_testing, temporal_address):
    """Run whitebox scan followed by blackbox verification."""
    from pathlib import Path

    from shannon_combined.orchestrator import run_combined_scan

    repo_path = str(Path(repo).resolve())
    click.echo(f"Starting combined scan: whitebox → blackbox")
    click.echo(f"  Repository: {repo_path}")
    click.echo(f"  Target URL: {url}")

    asyncio.run(ensure_infra(address=temporal_address))
    result = asyncio.run(run_combined_scan(
        repo_path=repo_path,
        url=url,
        temporal_address=temporal_address,
        config_path=config_path,
        pipeline_testing=pipeline_testing,
    ))

    if result.get("status") == "completed":
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
