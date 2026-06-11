"""Framework analyzer service.

Detects auto-generated REST framework usage (finale-rest, epilogue) and
infers endpoints that may not be visible in explicit route definitions.
Ported from /root/shannon/apps/worker/src/services/framework-analyzer.ts
and framework-patterns.ts.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EndpointTemplate:
    """Template for generating inferred endpoints from a model name."""

    methods: tuple[str, ...]
    path_template: str
    default_middleware: tuple[str, ...]
    notes: str


@dataclass(frozen=True)
class FrameworkPattern:
    """Detection pattern for an auto-REST framework."""

    name: str
    detection_patterns: dict[str, tuple[str, ...]]  # import, initialize, config
    endpoint_templates: tuple[EndpointTemplate, ...]
    vulnerability_patterns: tuple[str, ...]


@dataclass(frozen=True)
class InferredEndpoint:
    """An endpoint inferred from framework configuration."""

    method: str
    path: str
    source: str  # 'framework-auto-generated' | 'manual'
    model: str | None = None
    middleware: tuple[str, ...] = ()
    vulnerability_indicators: tuple[str, ...] = ()


@dataclass
class FrameworkAnalysisResult:
    """Result of framework analysis."""

    detected_framework: FrameworkPattern | None = None
    inferred_endpoints: list[InferredEndpoint] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Framework patterns (ported from framework-patterns.ts)
# ---------------------------------------------------------------------------

FRAMEWORK_PATTERNS: tuple[FrameworkPattern, ...] = (
    FrameworkPattern(
        name="finale-rest",
        detection_patterns={
            "import": (
                'require("express-finale")',
                'require("finale-rest")',
                "import.*finale.*from",
            ),
            "initialize": ("finale.initialize(", "finale.resource("),
            "config": ("finale.resource(",),
        },
        endpoint_templates=(
            EndpointTemplate(
                methods=("GET", "POST", "PUT", "DELETE"),
                path_template="/api/{Model}s",
                default_middleware=("isAuthenticated",),
                notes="Auto-generated CRUD operations, no ownership validation by default",
            ),
            EndpointTemplate(
                methods=("GET", "POST", "PUT", "DELETE"),
                path_template="/api/{Model}s/:id",
                default_middleware=("isAuthenticated",),
                notes="Individual resource operations, commonly vulnerable to IDOR",
            ),
        ),
        vulnerability_patterns=(
            "No ownership check on finale resource operations",
            "DELETE endpoint often unblocked by default",
            "PUT endpoint may lack role checks",
        ),
    ),
    FrameworkPattern(
        name="epilogue",
        detection_patterns={
            "import": ('require("epilogue")', "import.*epilogue.*from"),
            "initialize": ("epilogue.initialize(", "epilogue.resource("),
            "config": ("epilogue.resource(",),
        },
        endpoint_templates=(
            EndpointTemplate(
                methods=("GET", "POST", "PUT", "DELETE"),
                path_template="/api/{resource}",
                default_middleware=(),
                notes="Similar to finale, auto-generated CRUD",
            ),
        ),
        vulnerability_patterns=(
            "Epilogue resources lack ownership validation by default",
            "Mass operations enabled without explicit disable",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

async def analyze_frameworks(
    codebase_path: str,
) -> FrameworkAnalysisResult:
    """Detect auto-REST frameworks, discover models, infer endpoints, build recommendations."""
    detected: FrameworkPattern | None = None

    for pattern in FRAMEWORK_PATTERNS:
        if _detect_framework(codebase_path, pattern):
            detected = pattern
            logger.info("Detected framework: %s", pattern.name)
            break

    if detected is None:
        logger.info("No auto-generated REST framework detected")
        return FrameworkAnalysisResult()

    models = _discover_models(codebase_path, detected)
    logger.info("Found %d model(s) configured with %s: %s", len(models), detected.name, ", ".join(models))

    endpoints = _generate_inferred_endpoints(detected, models)
    recommendations = _build_recommendations(detected, endpoints)

    return FrameworkAnalysisResult(
        detected_framework=detected,
        inferred_endpoints=endpoints,
        recommendations=recommendations,
    )


def _detect_framework(codebase_path: str, pattern: FrameworkPattern) -> bool:
    """Scan source files for framework initialization patterns."""
    all_patterns = list(pattern.detection_patterns.get("import", ())) + list(
        pattern.detection_patterns.get("initialize", ())
    )
    if not all_patterns:
        return False

    try:
        source_files = _find_source_files(codebase_path)
        for file_path in source_files:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for detection_pattern in all_patterns:
                if re.search(detection_pattern, content):
                    logger.info('Framework pattern "%s" found in %s', detection_pattern, file_path)
                    return True
    except Exception as exc:
        logger.warning("Error scanning for framework %s: %s", pattern.name, exc)

    return False


def _find_source_files(codebase_path: str) -> list[Path]:
    """Find relevant source files to scan for framework patterns."""
    base = Path(codebase_path)
    files: list[Path] = []

    for candidate in ("server.js", "server.ts", "app.js", "app.ts", "index.js", "index.ts"):
        full = base / candidate
        if full.exists():
            files.append(full)

    for subdir in ("routes", "models", "api", "src/routes", "src/models"):
        dir_path = base / subdir
        if dir_path.exists():
            files.extend(dir_path.rglob("*.js"))
            files.extend(dir_path.rglob("*.ts"))

    return files


def _discover_models(codebase_path: str, pattern: FrameworkPattern) -> list[str]:
    """Discover model names configured with the framework."""
    models: list[str] = []
    # Guard: skip discovery if the framework has no resource config patterns defined.
    config_patterns = pattern.detection_patterns.get("config", ())
    if not config_patterns:
        return models

    model_regex = re.compile(r"\.resource\([^)]*?model\s*:\s*([A-Za-z_][A-Za-z0-9_]*)")

    try:
        source_files = _find_source_files(codebase_path)
        for file_path in source_files:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for match in model_regex.finditer(content):
                model_name = match.group(1)
                if model_name not in models:
                    models.append(model_name)
                    logger.info("Discovered model: %s in %s", model_name, file_path)
    except Exception as exc:
        logger.warning("Error discovering models for %s: %s", pattern.name, exc)

    return models


def _generate_inferred_endpoints(
    framework: FrameworkPattern, models: Sequence[str]
) -> list[InferredEndpoint]:
    """Generate inferred endpoints from framework templates and discovered models."""
    endpoints: list[InferredEndpoint] = []

    for model in models:
        for template in framework.endpoint_templates:
            base_path = template.path_template.replace("{Model}", model).replace(
                "{resource}", model.lower()
            )
            is_collection = ":id" not in template.path_template

            for method in template.methods:
                # Collection endpoints skip PUT and DELETE
                if is_collection and method in ("PUT", "DELETE"):
                    continue

                endpoints.append(
                    InferredEndpoint(
                        method=method,
                        path=base_path,
                        source="framework-auto-generated",
                        model=model,
                        middleware=template.default_middleware,
                        vulnerability_indicators=framework.vulnerability_patterns,
                    )
                )

    return endpoints


def _build_recommendations(
    framework: FrameworkPattern, endpoints: Sequence[InferredEndpoint]
) -> list[str]:
    """Build security recommendations based on detected framework and endpoints."""
    recs: list[str] = [
        f"Framework {framework.name} detected — auto-generated endpoints may lack ownership validation"
    ]

    delete_endpoints = [ep for ep in endpoints if ep.method == "DELETE"]
    if delete_endpoints:
        recs.append(
            f"{len(delete_endpoints)} DELETE endpoint(s) auto-generated — verify each has authorization guards"
        )

    put_endpoints = [ep for ep in endpoints if ep.method == "PUT"]
    if put_endpoints:
        recs.append(f"{len(put_endpoints)} PUT endpoint(s) auto-generated — verify role-based access control")

    recs.extend(framework.vulnerability_patterns)
    return recs
