import asyncio
from pathlib import Path
import click
from shannon_multi.orchestrator import run_cross_repo


@click.group()
def cli():
    """Shannon cross-repo microservice correlation orchestrator."""


@cli.command()
@click.option("-c", "--config", "config_path", required=True,
              type=click.Path(exists=True, dir_okay=False),
              help="multi-repo.yaml path")
@click.option("--temporal-address", default="localhost:7233")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts (CI)")
def start(config_path, temporal_address, pipeline_testing):
    """Orchestrate multi-repo whitebox scans + cross-repo correlation."""
    result = asyncio.run(run_cross_repo(Path(config_path), temporal_address,
                                        pipeline_testing=pipeline_testing))
    click.echo(f"Correlation workspace: {result['out_workspace']}")


def main():
    cli()


if __name__ == "__main__":
    main()
