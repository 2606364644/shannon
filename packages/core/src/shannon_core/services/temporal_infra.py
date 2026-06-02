"""Temporal infrastructure management — health checks, start/stop, auto-ensure."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from subprocess import CalledProcessError
from pathlib import Path

from temporalio.client import Client

logger = logging.getLogger(__name__)

# Resolve project root: this file is at packages/core/src/shannon_core/services/temporal_infra.py
_PROJECT_ROOT = Path(__file__).resolve().parents[5]

_CONTAINER_NAME = "shannon-py-temporal"
_READY_POLL_ATTEMPTS = 30
_READY_POLL_INTERVAL = 2  # seconds


def get_compose_file(path: Path | None = None) -> Path:
    """Return the docker-compose.yml path. Defaults to project root."""
    if path is not None:
        return path
    return _PROJECT_ROOT / "docker-compose.yml"


async def is_temporal_ready(address: str = "localhost:7233") -> bool:
    """Check whether the Temporal server is reachable and healthy."""
    try:
        client = await Client.connect(address)
        await client.close()
        return True
    except Exception:
        return False


def start_temporal(compose_file: Path | None = None) -> None:
    """Start the Temporal container via docker compose."""
    compose = get_compose_file(compose_file)
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(compose), "up", "-d"],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("Temporal container started")
    except CalledProcessError as e:
        raise RuntimeError(f"Failed to start Temporal: {e.stderr}") from e
    except FileNotFoundError as e:
        raise RuntimeError(
            "docker command not found. Please install Docker and ensure it is in PATH."
        ) from e


def stop_temporal(compose_file: Path | None = None) -> None:
    """Stop the Temporal container via docker compose."""
    compose = get_compose_file(compose_file)
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(compose), "down"],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("Temporal container stopped")
    except CalledProcessError as e:
        raise RuntimeError(f"Failed to stop Temporal: {e.stderr}") from e
    except FileNotFoundError as e:
        raise RuntimeError(
            "docker command not found. Please install Docker and ensure it is in PATH."
        ) from e


async def get_temporal_status(
    compose_file: Path | None = None,
    address: str = "localhost:7233",
) -> dict:
    """Return Temporal container and health status."""
    compose = get_compose_file(compose_file)
    container_status = "unknown"
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose), "ps", "--format", "{{.Status}}"],
            capture_output=True,
            text=True,
        )
        stdout = result.stdout.strip().lower()
        if "up" in stdout:
            container_status = "running"
        elif not stdout:
            container_status = "stopped"
        else:
            container_status = stdout
    except FileNotFoundError:
        container_status = "not found"

    healthy = await is_temporal_ready(address)
    return {"container": container_status, "healthy": healthy}


async def ensure_infra(
    compose_file: Path | None = None,
    address: str = "localhost:7233",
) -> None:
    """Ensure Temporal infrastructure is available.

    1. If Temporal is already ready, return immediately.
    2. Otherwise, start the container via docker compose.
    3. Poll until healthy (30 attempts × 2s interval).
    4. Raise RuntimeError on timeout.
    """
    if await is_temporal_ready(address):
        return

    logger.info("Temporal not ready — starting infrastructure...")
    start_temporal(compose_file)

    logger.info("Waiting for Temporal to become ready...")
    for i in range(_READY_POLL_ATTEMPTS):
        if await is_temporal_ready(address):
            logger.info("Temporal is ready!")
            return
        await asyncio.sleep(_READY_POLL_INTERVAL)

    raise RuntimeError(
        "Timed out waiting for Temporal to become ready. "
        "Check `docker compose logs` for errors."
    )
