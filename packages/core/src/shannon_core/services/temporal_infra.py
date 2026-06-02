"""Temporal infrastructure management — health checks, start/stop, auto-ensure."""

from __future__ import annotations

import logging
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
