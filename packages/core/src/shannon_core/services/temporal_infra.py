"""Temporal infrastructure management — health checks, start/stop, auto-ensure."""

from __future__ import annotations

import asyncio
import logging
import secrets
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


def generate_task_queue(prefix: str) -> str:
    """Generate a unique task queue name: {prefix}-{8-char-hex}."""
    suffix = secrets.token_hex(4)
    return f"{prefix}-{suffix}"


def _shannon_container_exists() -> bool:
    """Check if the original shannon-temporal container exists (running or stopped)."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=shannon-temporal", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
        )
        return "shannon-temporal" in result.stdout.strip()
    except FileNotFoundError:
        return False


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

    Priority chain:
    1. If Temporal is already reachable, return immediately.
    2. If the original shannon-temporal container exists (stopped), start it.
    3. Otherwise, start shannon-py's own docker-compose as fallback.
    """
    # Step 1: Already reachable?
    if await is_temporal_ready(address):
        logger.info("Temporal already reachable at %s — reusing.", address)
        return

    # Step 2: Original project container exists but stopped?
    if _shannon_container_exists():
        logger.info("Found shannon-temporal container — starting it.")
        try:
            subprocess.run(
                ["docker", "start", "shannon-temporal"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (CalledProcessError, FileNotFoundError) as e:
            raise RuntimeError(f"Failed to start shannon-temporal: {e}") from e
    else:
        # Step 3: Start our own
        logger.info("No existing Temporal found — starting shannon-py container.")
        start_temporal(compose_file)

    # Poll until ready
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
