"""recon GitNexus deterministic track for shared routes and endpoint security.

The outputs here are a lower bound for the recon LLM. They are not a verdict
queue merge product; recon merges these markdown hints into its §4.1/§4.2
deliverable with field-level dangerous-side rules.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from shannon_core.code_index.models import CodeIndex, EntryPoint, FuncBlock

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


_AUTH_GUARD_RE = re.compile(
    r"(?i)\b("
    r"@?RequireAuth|@?requireAuth|@?IsAuthenticated|@?isAuthenticated|"
    r"@?UseGuards|@?Guard|@?PreAuthorize|@?Secured|@?RolesAllowed|"
    r"@?login_required|@?loginRequired|@?auth_required|@?AuthRequired|"
    r"AuthGuard|canActivate|@Authorize|@AuthorizeRequest"
    r")\b"
)
_EXPLICIT_PUBLIC_RE = re.compile(r"(?i)\b(public|@?Anonymous|allowAnonymous|noAuth)\b")
_MIDDLEWARE_NAME_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|;|\s*$|\s*[,)])")
_MIDDLEWARE_HINT_RE = re.compile(
    r"(?i)(\.use\s*\(|\.guard\s*\(|middleware\s*[:=]|Guard\b|requireAdmin|requireRole)"
)
_OWNERSHIP_PREDICATE_RE = re.compile(
    r"(?is)("
    r"where\s*[:(][^}\n;]{0,240}\b(user_?id|owner_?id|owner|creator_?id|author_?id)\b"
    r"[^}\n;]{0,240}(req\.user|ctx\.state\.user|currentUser|user\.id|userId)"
    r"|\.where\s*\(\s*['\"]?(user_?id|owner_?id|owner|creator_?id|author_?id)['\"]?\s*[,=]"
    r"|\bfindBy(Owner|OwnerId|UserId|CreatorId|AuthorId)\b"
    r"|\b(owner|currentUser|req\.user|ctx\.state\.user)\s*\.\s*id\b"
    r"|\b(user_?id|owner_?id)\s*=\s*(req|ctx|currentUser)"
    r")"
)


@dataclass(frozen=True)
class EndpointSecurityContext:
    """§4.2 endpoint security context from deterministic source scanning."""

    method: str | None
    path: str
    handler_id: str
    auth: str
    middleware: tuple[str, ...]
    ownership: str
    ownership_evidence: str | None


def _detect_auth(source: str, decorators: list[str], ep_auth: str | None) -> str:
    """Determine auth presence from source, decorators, and entry-point hints."""
    blob = source + "\n" + " ".join(decorators)
    if _AUTH_GUARD_RE.search(blob):
        return "present"
    if _EXPLICIT_PUBLIC_RE.search(blob):
        return "none"
    if ep_auth is not None:
        return _auth_token(ep_auth)
    return "none"


def _extract_middleware(source: str, decorators: list[str]) -> tuple[str, ...]:
    """Extract likely middleware or guard names from source text."""
    blob = source + "\n" + " ".join(decorators)
    found: list[str] = []

    for decorator in decorators:
        match = re.match(r"@?([A-Za-z_][A-Za-z0-9_]*)", decorator)
        if match and match.group(1) not in found:
            found.append(match.group(1))

    if _MIDDLEWARE_HINT_RE.search(blob):
        for match in _MIDDLEWARE_NAME_RE.finditer(blob):
            name = match.group(1)
            if re.search(r"(?i)(auth|role|admin|guard|middleware)", name) and name not in found:
                found.append(name)

    return tuple(found)


def _detect_ownership(source: str) -> tuple[str, str | None]:
    """Return ownership candidate and a short evidence snippet."""
    match = _OWNERSHIP_PREDICATE_RE.search(source)
    if match is None:
        return "none", None

    start = max(0, match.start() - 20)
    end = min(len(source), match.end() + 20)
    return "guarded", source[start:end].replace("\n", " ").strip()


def scan_endpoint_security(
    entry_points: list[EntryPoint],
    blocks_by_id: dict[str, FuncBlock],
) -> list[EndpointSecurityContext]:
    """Build §4.2 deterministic auth, middleware, and ownership context."""
    contexts: list[EndpointSecurityContext] = []

    for ep in entry_points:
        if ep.route is None:
            continue

        block = blocks_by_id.get(ep.func_block_id)
        if block is None:
            contexts.append(
                EndpointSecurityContext(
                    method=ep.http_method,
                    path=ep.route,
                    handler_id=ep.func_block_id,
                    auth="unknown",
                    middleware=(),
                    ownership="unknown",
                    ownership_evidence=None,
                )
            )
            continue

        ownership, evidence = _detect_ownership(block.source_code)
        contexts.append(
            EndpointSecurityContext(
                method=ep.http_method,
                path=ep.route,
                handler_id=ep.func_block_id,
                auth=_detect_auth(block.source_code, list(block.decorators), ep.authentication),
                middleware=_extract_middleware(block.source_code, list(block.decorators)),
                ownership=ownership,
                ownership_evidence=evidence,
            )
        )

    logger.info(
        "recon §4.2 track: %d endpoints (auth present=%d none=%d unknown=%d; "
        "ownership guarded=%d none=%d unknown=%d)",
        len(contexts),
        sum(1 for context in contexts if context.auth == "present"),
        sum(1 for context in contexts if context.auth == "none"),
        sum(1 for context in contexts if context.auth == "unknown"),
        sum(1 for context in contexts if context.ownership == "guarded"),
        sum(1 for context in contexts if context.ownership == "none"),
        sum(1 for context in contexts if context.ownership == "unknown"),
    )
    return contexts


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _auth_cell(auth: str) -> str:
    if auth == "none":
        return "**none**"
    return auth


def render_recon_gitnexus_track(
    groups: list[SharedRouteGroup],
    contexts: list[EndpointSecurityContext],
) -> str:
    """Render deterministic recon intelligence as prompt markdown."""
    if not groups and not contexts:
        return "（无确定性检测到的共享路由组或端点安全上下文。GitNexus 索引可能未就绪或为空。）"

    lines: list[str] = [
        "## Recon GitNexus Track（确定性下限：§4.1 路由组 + §4.2 端点安全上下文）",
        "",
    ]

    if groups:
        lines.extend(
            [
                "### §4.1 Shared Route Groups（调用图反推：同 handler 多路由）",
                "",
                "| Handler | Method | Path | Auth |",
                "|---|---|---|---|",
            ]
        )
        for group in groups:
            for route in group.routes:
                lines.append(
                    f"| `{_md_cell(group.handler_id)}` | {route.method or '-'} | "
                    f"`{_md_cell(route.path)}` | {_auth_cell(route.auth)} |"
                )
            if group.auth_conflict:
                lines.append("")
                lines.append(
                    f"> `{_md_cell(group.handler_id)}`: at least one route is not clearly authenticated; "
                    "pre-auth variant candidate, take the dangerous side."
                )
        lines.append("")

    if contexts:
        lines.extend(
            [
                "### §4.2 Endpoint Security Context（确定性：auth/middleware/ownership）",
                "",
                "| Endpoint | Handler | Auth | Middleware | Ownership | Evidence |",
                "|---|---|---|---|---|---|",
            ]
        )
        for context in contexts:
            endpoint = f"{context.method or '-'} {context.path}"
            middleware = ", ".join(context.middleware) if context.middleware else "-"
            ownership = "**none**" if context.ownership == "none" else context.ownership
            evidence = context.ownership_evidence or "-"
            lines.append(
                f"| `{_md_cell(endpoint)}` | `{_md_cell(context.handler_id)}` | "
                f"{_auth_cell(context.auth)} | {_md_cell(middleware)} | {ownership} | "
                f"`{_md_cell(evidence)}` |"
            )
        lines.append("")

    lines.extend(
        [
            "**填充与合并规则（字段危险侧，spec §4.3）：**",
            "- 以上为确定性检测下限。recon §4.1/§4.2 表须据此填充，并独立探索其他路由组/端点；下限非上限。",
            "- Auth 冲突取无：任一轨标 none 或未清晰认证，合并取 none。",
            "- Framework Origin 冲突取 auto-generated：确定性轨标 auto-generated 即取 auto-generated。",
            "- Ownership 冲突取 none：任一轨标 none，合并取 none。guarded 仅为候选，须语义确认。",
            "- Missing handler 标 unknown：handler 未在 code_index 解析到源码，必须独立核实。",
        ]
    )
    return "\n".join(lines)


def build_recon_gitnexus_track(deliverables_dir: str) -> str:
    """Read code_index.json and render the deterministic recon track.

    This is intentionally lenient: index absence, invalid JSON, and schema drift
    all return the same empty-track markdown so recon can still run.
    """
    index_path = Path(deliverables_dir) / "code_index.json"
    if not index_path.exists():
        logger.info("recon GitNexus track: code_index.json missing")
        return render_recon_gitnexus_track([], [])

    try:
        index = CodeIndex.model_validate_json(index_path.read_text())
    except Exception as exc:
        logger.warning("recon GitNexus track: code_index.json parse failed: %s", exc)
        return render_recon_gitnexus_track([], [])

    if not index.entry_points:
        logger.info("recon GitNexus track: code_index.json has no entry_points")
        return render_recon_gitnexus_track([], [])

    blocks_by_id = {block.id: block for block in index.blocks}
    groups = detect_shared_route_groups(list(index.entry_points))
    contexts = scan_endpoint_security(list(index.entry_points), blocks_by_id)
    return render_recon_gitnexus_track(groups, contexts)
