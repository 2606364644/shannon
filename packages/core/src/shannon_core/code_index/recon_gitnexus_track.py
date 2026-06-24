"""recon GitNexus deterministic track for shared routes and endpoint security.

The outputs here are a lower bound for the recon LLM. They are not a verdict
queue merge product; recon merges these markdown hints into its §4.1/§4.2
deliverable with field-level dangerous-side rules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from shannon_core.code_index.models import EntryPoint

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteRow:
    """One route in a shared-route group."""

    method: str | None
    path: str
    auth: str


@dataclass(frozen=True)
class SharedRouteGroup:
    """§4.1 group: two or more routes mapped to the same handler."""

    handler_id: str
    routes: tuple[RouteRow, ...]
    auth_conflict: bool


def _auth_token(auth: str | None) -> str:
    """Normalize EntryPoint.authentication to present/none/unknown."""
    if auth is None:
        return "unknown"

    token = auth.strip().lower()
    if token in {"public", "none", "no", "missing", "absent"}:
        return "none"
    if token in {"required", "user", "admin"}:
        return "present"
    return "unknown"


def detect_shared_route_groups(entry_points: list[EntryPoint]) -> list[SharedRouteGroup]:
    """Group entry points by handler and keep handlers with multiple routes."""
    by_handler: dict[str, list[EntryPoint]] = {}
    for ep in entry_points:
        if ep.route is None:
            continue
        by_handler.setdefault(ep.func_block_id, []).append(ep)

    groups: list[SharedRouteGroup] = []
    for handler_id, eps in by_handler.items():
        seen: set[tuple[str | None, str]] = set()
        rows: list[RouteRow] = []
        for ep in eps:
            key = (ep.http_method, ep.route)
            if key in seen:
                continue
            seen.add(key)
            rows.append(RouteRow(method=ep.http_method, path=ep.route, auth=_auth_token(ep.authentication)))

        if len(rows) < 2:
            continue

        groups.append(
            SharedRouteGroup(
                handler_id=handler_id,
                routes=tuple(rows),
                auth_conflict=any(row.auth != "present" for row in rows),
            )
        )

    logger.info(
        "recon §4.1 track: %d handlers -> %d shared-route groups",
        len(by_handler),
        len(groups),
    )
    return groups
