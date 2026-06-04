"""Multi-source entry point fusion.

Merges entry points from:
1. GitNexus EP Scoring (primary, highest confidence)
2. Schema files (OpenAPI/GraphQL/Proto → handler)
3. Framework conventions (Next.js pages/api/, Django urls.py)

Deduplicates by uid (file_path:name), keeping the highest-confidence source.
"""

import logging

from shannon_core.code_index.models import UnifiedEntryPoint

logger = logging.getLogger(__name__)


def merge_entry_points(
    gitnexus_eps: list[dict],
    schema_eps: list[UnifiedEntryPoint],
    convention_eps: list[UnifiedEntryPoint],
) -> list[UnifiedEntryPoint]:
    """Merge entry points from multiple sources.

    Priority order for dedup: gitnexus > schema > convention.
    Each source gets a confidence score:
    - gitnexus: from EP Scoring (variable)
    - schema_file: 0.80 (high trust, but not code-verified)
    - framework_convention: 0.75 (convention-based, good trust)

    Args:
        gitnexus_eps: Entry points from GitNexus EP Scoring (MCP cypher results).
        schema_eps: Entry points from Schema file parsing.
        convention_eps: Entry points from framework convention detection.

    Returns:
        Deduplicated list of UnifiedEntryPoint sorted by confidence descending.
    """
    unified: dict[str, UnifiedEntryPoint] = {}

    # Source 1: GitNexus EP Scoring (primary)
    for ep in gitnexus_eps:
        name = ep.get("name", "")
        file_path = ep.get("filePath", "")
        key = f"{file_path}:{name}"
        if key not in unified:
            unified[key] = UnifiedEntryPoint(
                uid=key,
                name=name,
                file_path=file_path,
                confidence=ep.get("score", 0.5),
                source="gitnexus",
                entry_type=ep.get("kind", "unknown"),
                route=ep.get("route"),
                http_method=ep.get("httpMethod"),
                evidence=f"GitNexus EP Scoring (score={ep.get('score', 0.5):.2f})",
            )

    # Source 2: Schema files (OpenAPI/GraphQL/Proto)
    for ep in schema_eps:
        if ep.uid not in unified:
            unified[ep.uid] = ep

    # Source 3: Framework conventions (Next.js, Django, etc.)
    for ep in convention_eps:
        if ep.uid not in unified:
            unified[ep.uid] = ep

    # Sort by confidence descending
    result = sorted(unified.values(), key=lambda ep: -ep.confidence)

    logger.info(
        "Merged %d entry points: %d from GitNexus, %d from schema, %d from convention",
        len(result),
        sum(1 for e in result if e.source == "gitnexus"),
        sum(1 for e in result if e.source == "schema_file"),
        sum(1 for e in result if e.source == "framework_convention"),
    )

    return result
