"""Frontend route mapper.

Maps frontend routes to their data sources and API calls to identify
potential multi-step attack chains (e.g., stored XSS via user input ->
API storage -> admin panel rendering).
Ported from /root/shannon/apps/worker/src/services/frontend-mapper.ts
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UserInputPoint:
    type: str  # 'url-param' | 'query-param' | 'body' | 'header'
    field: str
    sanitization: str | None = None


@dataclass(frozen=True)
class ApiCall:
    endpoint: str
    method: str
    purpose: str
    data_flow: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrontendRoute:
    path: str
    component: str
    authenticated: bool
    api_calls: tuple[ApiCall, ...] = ()
    user_inputs: tuple[UserInputPoint, ...] = ()


@dataclass(frozen=True)
class XssAttackChain:
    entry_point: str
    storage_endpoint: str
    render_endpoint: str
    sink: str
    confidence: str  # 'high' | 'medium' | 'low'


@dataclass
class FrontendAnalysisResult:
    routes: list[FrontendRoute] = field(default_factory=list)
    xss_chains: list[XssAttackChain] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

async def map_frontend_routes(codebase_path: str) -> FrontendAnalysisResult:
    """Detect frontend framework, find route files, parse routes, identify XSS chains."""
    routes: list[FrontendRoute] = []

    framework = await _detect_frontend_framework(codebase_path)
    logger.info("Detected frontend framework: %s", framework)

    route_files = _find_route_files(codebase_path, framework)
    if not route_files:
        logger.info("No frontend route files found")
        return FrontendAnalysisResult()

    logger.info("Found %d route file(s): %s", len(route_files), ", ".join(str(f) for f in route_files))

    for file_path in route_files:
        file_routes = _parse_routes(file_path, framework)
        routes.extend(file_routes)

    xss_chains = identify_xss_chains(routes)
    logger.info("Mapped %d route(s), identified %d potential XSS chain(s)", len(routes), len(xss_chains))

    return FrontendAnalysisResult(routes=routes, xss_chains=xss_chains)


async def _detect_frontend_framework(codebase_path: str) -> str:
    """Detect which frontend framework is in use."""
    pkg_path = Path(codebase_path) / "package.json"
    if not pkg_path.exists():
        return "unknown"

    try:
        content = pkg_path.read_text(encoding="utf-8", errors="ignore")
        if '"@angular/core"' in content:
            return "angular"
        if '"react"' in content or '"next"' in content:
            return "react"
        if '"vue"' in content or '"nuxt"' in content:
            return "vue"
    except Exception as exc:
        logger.warning("Error reading package.json: %s", exc)

    return "unknown"


def _find_route_files(codebase_path: str, framework: str) -> list[Path]:
    """Find frontend route definition files based on framework."""
    base = Path(codebase_path)
    files: list[Path] = []

    search_dirs = [
        base / "frontend" / "src",
        base / "frontend",
        base / "src" / "app",
        base / "src",
        base,
    ]

    filename_patterns: dict[str, list[str]] = {
        "angular": ["app-routing.module.ts", "app.routes.ts", "routes.ts"],
        "react": ["routes.tsx", "routes.ts", "router.tsx", "router.ts", "App.tsx"],
        "vue": ["router.ts", "router.js", "index.ts", "index.js"],
        "unknown": ["routes.ts", "routes.tsx", "router.ts", "router.tsx", "app.routes.ts"],
    }

    patterns = filename_patterns.get(framework, filename_patterns["unknown"])

    for dir_path in search_dirs:
        if not dir_path.exists():
            continue
        for pattern in patterns:
            file_path = dir_path / pattern
            if file_path.exists():
                files.append(file_path)

    return files


def _parse_routes(file_path: Path, framework: str) -> list[FrontendRoute]:
    """Parse route definitions from a file."""
    routes: list[FrontendRoute] = []

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")

        route_regex_map: dict[str, re.Pattern[str]] = {
            "angular": re.compile(
                r"path\s*:\s*['\"`]([^'\"`]+)['\"`][^}]*?component\s*:\s*([A-Za-z_][A-Za-z0-9_]*)"
            ),
            "react": re.compile(
                r"path\s*:\s*['\"`]([^'\"`]+)['\"`][^}]*?(?:element|component)\s*:\s*(?:<|([A-Za-z_][A-Za-z0-9_]*))"
            ),
            "vue": re.compile(
                r"path\s*:\s*['\"`]([^'\"`]+)['\"`][^}]*?(?:component|name)\s*:\s*['\"`]?([A-Za-z_][A-Za-z0-9_]*)"
            ),
        }

        regex = route_regex_map.get(framework, re.compile(r"path\s*:\s*['\"`]([^'\"`]+)['\"`]"))

        for match in regex.finditer(content):
            path = match.group(1)
            component = match.group(2) if match.lastindex and match.lastindex >= 2 else "Unknown"
            if path:
                routes.append(
                    FrontendRoute(
                        path=path,
                        component=component or "Unknown",
                        authenticated=_has_auth_guard(content),
                        api_calls=(),
                        user_inputs=(),
                    )
                )
    except Exception as exc:
        logger.warning("Error parsing routes from %s: %s", file_path, exc)

    return routes


def _has_auth_guard(content: str) -> bool:
    """Check if content contains common auth guard patterns."""
    return any(
        guard in content
        for guard in ("AuthGuard", "canActivate", "requireAuth")
    )


def identify_xss_chains(routes: list[FrontendRoute]) -> list[XssAttackChain]:
    """Identify potential XSS attack chains from frontend routes."""
    chains: list[XssAttackChain] = []

    input_routes = [
        r for r in routes
        if r.user_inputs or any(a.method == "POST" for a in r.api_calls)
    ]
    render_routes = [r for r in routes if any(a.method == "GET" for a in r.api_calls)]

    for input_route in input_routes:
        for api_call in input_route.api_calls:
            if api_call.method != "POST":
                continue
            for render_route in render_routes:
                for render_api in render_route.api_calls:
                    if render_api.method != "GET":
                        continue
                    storage_base = extract_base_path(api_call.endpoint)
                    render_base = extract_base_path(render_api.endpoint)
                    if storage_base and render_base and storage_base == render_base:
                        chains.append(
                            XssAttackChain(
                                entry_point=input_route.path,
                                storage_endpoint=api_call.endpoint,
                                render_endpoint=render_route.path,
                                sink=render_route.component,
                                confidence="medium",
                            )
                        )

    return chains


def extract_base_path(endpoint: str) -> str:
    """Extract the base path from an API endpoint (e.g., /api/Users from /api/Users/:id)."""
    parts = endpoint.split("/")
    return "/".join(p for p in parts if not p.startswith(":"))
