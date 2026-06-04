"""Security file discovery — template, config, and schema files.

GitNexus handles source code (14 languages via Tree-sitter).
This module discovers security-critical file types that GitNexus
does NOT cover: templates, configs, schemas, and SQL queries.
"""

import logging
from pathlib import Path

from shannon_core.code_index.models import FileEntry, FileManifest

logger = logging.getLogger(__name__)

SECURITY_FILE_TYPES: dict[str, set[str]] = {
    "template": {".html", ".ejs", ".pug", ".hbs", ".jinja2", ".j2",
                 ".vue", ".svelte", ".erb", ".tmpl"},
    "config":   {".yaml", ".yml", ".json", ".toml", ".xml", ".env", ".ini"},
    "schema":   {".graphql", ".gql", ".proto", ".thrift"},
    "query":    {".sql"},
}

# Build a flat lookup: extension → file_type
_EXT_TO_TYPE: dict[str, str] = {}
for ftype, exts in SECURITY_FILE_TYPES.items():
    for ext in exts:
        _EXT_TO_TYPE[ext] = ftype

SKIP_DIRS: set[str] = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".venv",
    "venv", "env", ".eggs", "eggs", ".gitnexus",
}


def classify_security_file(suffix: str) -> str | None:
    """Classify a file suffix as a security file type, or None if not security-relevant."""
    return _EXT_TO_TYPE.get(suffix.lower())


def discover_security_files(repo_root: Path) -> FileManifest:
    """Walk the repo and discover all security-relevant files.

    Skips .git, node_modules, vendor, and other non-source directories.
    """
    entries: list[FileEntry] = []

    for file_path in repo_root.rglob("*"):
        if not file_path.is_file():
            continue

        relative = file_path.relative_to(repo_root)

        # Skip hidden/vendored directories
        skip = False
        for part in relative.parts:
            if part in SKIP_DIRS or part.startswith("."):
                skip = True
                break
        if skip:
            continue

        file_type = classify_security_file(file_path.suffix.lower())
        if file_type is None:
            continue

        entries.append(FileEntry(
            file_path=str(relative),
            file_type=file_type,
            size_bytes=file_path.stat().st_size,
        ))

    logger.info("Discovered %d security files: %s", len(entries),
                dict(FileManifest(entries=entries).by_type))

    return FileManifest(entries=entries)
