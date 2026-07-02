"""OpenAPI / Swagger schema file parser → EntryPoint list.

Scans repo for openapi.{yaml,yml,json} / swagger.{yaml,yml,json}, parses
`paths` → one EntryPoint per (method, path). These are high-trust route
declarations (explicit, code-verified=False) that supplement code-level
entry point detection, especially for authz candidate generation where
code-level handlers may be missed.

Parse failures are non-fatal (warning + skip), per spec R4.
"""

import json
import logging
import os
from pathlib import Path

import yaml

from shannon_core.code_index.models import EntryPoint

logger = logging.getLogger(__name__)

_OPENAPI_FILENAMES = {
    "openapi.yaml", "openapi.yml", "openapi.json",
    "swagger.yaml", "swagger.yml", "swagger.json",
}
_SKIP_DIRS = {"node_modules", ".git", "dist", "build", "vendor", ".venv", "__pycache__", ".next"}
_VALID_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}


def _find_openapi_files(repo_path: str) -> list[Path]:
    repo = Path(repo_path)
    found: list[Path] = []
    if not repo.exists():
        return found
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            if f.lower() in _OPENAPI_FILENAMES:
                found.append(Path(root) / f)
    return found


def _load_spec(path: Path) -> dict | None:
    """Load an OpenAPI/Swagger spec. Returns None on parse failure (non-fatal)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() == ".json":
            return json.loads(text)
        return yaml.safe_load(text)
    except Exception as e:  # parse failure → skip this file
        logger.warning("OpenAPI parse failed for %s: %s (skipping)", path, e)
        return None


def _has_security(op: dict, path_item: dict, spec: dict | None) -> bool:
    """operation-level security > path-level > root-level (OpenAPI inheritance)."""
    if "security" in op:
        return bool(op["security"])
    if "security" in path_item:
        return bool(path_item["security"])
    if spec and "security" in spec:
        return bool(spec["security"])
    return False


def parse_openapi_schema_files(repo_path: str) -> list[EntryPoint]:
    """Parse all OpenAPI/Swagger files under repo_path → EntryPoint list.

    Each (method, path) under `paths` yields one EntryPoint (source="schema_file",
    confidence=0.80, needs_llm_review=True). Non-OpenAPI files and parse failures
    are skipped silently (warning logged). Returns [] if repo has no spec.
    """
    entry_points: list[EntryPoint] = []
    repo = Path(repo_path)

    for spec_path in _find_openapi_files(repo_path):
        spec = _load_spec(spec_path)
        if not isinstance(spec, dict):
            continue
        paths = spec.get("paths")
        if not isinstance(paths, dict):
            continue

        spec_rel = spec_path.relative_to(repo).as_posix()

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method, op in path_item.items():
                m = method.upper()
                if m not in _VALID_METHODS:
                    continue  # skip parameters/summary etc.
                if not isinstance(op, dict):
                    op = {}
                authentication = "required" if _has_security(op, path_item, spec) else None
                entry_points.append(EntryPoint(
                    func_block_id=f"openapi:{spec_rel}:{m}:{path}",
                    entry_type="http_route",
                    route=path,
                    http_method=m,
                    confidence=0.80,
                    evidence=f"OpenAPI schema: {spec_rel} {m} {path}",
                    needs_llm_review=True,
                    authentication=authentication,
                    source="schema_file",
                ))

    logger.info("OpenAPI schema parse: %d entry points from %s", len(entry_points), repo)
    return entry_points
