"""Multi-source entry point fusion.

Merges entry points from:
1. GitNexus EP Scoring (primary, highest confidence)
2. Schema files (OpenAPI/GraphQL/Proto → handler)
3. Framework conventions (Next.js pages/api/, Django urls.py)

Deduplicates by uid (file_path:name), keeping the highest-confidence source.
"""

import logging
import re

from shannon_core.code_index.models import EntryPoint, UnifiedEntryPoint

logger = logging.getLogger(__name__)


def merge_entry_points(
    gitnexus_eps: list[dict],
    schema_eps: list[UnifiedEntryPoint],
    convention_eps: list[UnifiedEntryPoint],
    llm_eps: list[EntryPoint] | None = None,
) -> list[UnifiedEntryPoint]:
    """Merge entry points from multiple sources.

    Priority order for dedup: gitnexus > schema > convention > llm_pre_recon.
    Each source gets a confidence score:
    - gitnexus: from EP Scoring (variable)
    - schema_file: 0.80 (high trust, but not code-verified)
    - framework_convention: 0.75 (convention-based, good trust)
    - llm_pre_recon: 0.60 (LLM-discovered, lower default confidence)

    Args:
        gitnexus_eps: Entry points from GitNexus EP Scoring (MCP cypher results).
        schema_eps: Entry points from Schema file parsing.
        convention_eps: Entry points from framework convention detection.
        llm_eps: Entry points discovered by LLM pre-recon Entry Point Mapper.

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

    # Source 4: LLM pre-recon discoveries
    for ep in llm_eps or []:
        # Dedup key: extract file:function from func_block_id
        key = ep.func_block_id
        if key not in unified:
            # Extract function name from func_block_id
            parts = ep.func_block_id.split(":")
            name = parts[1] if len(parts) >= 2 else ep.func_block_id
            file_path = parts[0] if len(parts) >= 2 else ""

            unified[key] = UnifiedEntryPoint(
                uid=key,
                name=name,
                file_path=file_path,
                confidence=ep.confidence,
                source="llm_pre_recon",
                entry_type=ep.entry_type,
                route=ep.route,
                http_method=ep.http_method,
                evidence=ep.evidence,
            )

    # Sort by confidence descending
    result = sorted(unified.values(), key=lambda ep: -ep.confidence)

    logger.info(
        "Merged %d entry points: %d from GitNexus, %d from schema, %d from convention, %d from LLM",
        len(result),
        sum(1 for e in result if e.source == "gitnexus"),
        sum(1 for e in result if e.source == "schema_file"),
        sum(1 for e in result if e.source == "framework_convention"),
        sum(1 for e in result if e.source == "llm_pre_recon"),
    )

    return result


def parse_llm_entry_points(deliverable_text: str) -> list[EntryPoint]:
    """Parse LLM-discovered entry points from pre-recon deliverable Markdown.

    Looks for the "Attack Surface Analysis" section and extracts structured
    entry point information using regex patterns.

    Args:
        deliverable_text: Full text of pre_recon_deliverable.md.

    Returns:
        List of EntryPoint objects with confidence=0.60 and source="llm_pre_recon".
        Returns empty list on parse failures (never raises).
    """
    if not deliverable_text:
        return []

    entry_points: list[EntryPoint] = []

    # Find the Attack Surface Analysis section
    section_match = re.search(
        r"## 5\. Attack Surface Analysis(.*?)(?=## \d|$)",
        deliverable_text,
        re.DOTALL | re.IGNORECASE,
    )
    if not section_match:
        return []

    section = section_match.group(1)

    # Pattern 1: **METHOD /path** — `file.py:func_name` (line N)
    route_pattern = re.compile(
        r"\**(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/[^\s*]*)\**\s*"
        r"[—\-–]\s*`([^`]+)`",
        re.IGNORECASE,
    )
    for m in route_pattern.finditer(section):
        route_path = m.group(1).strip()
        func_ref = m.group(2).strip()
        # Parse file:function
        parts = func_ref.rsplit(":", 1)
        if len(parts) >= 2:
            file_path = parts[0]
            func_name = parts[1]
        else:
            file_path = func_ref
            func_name = "unknown"

        # Extract HTTP method from the matched text
        method_match = re.match(
            r"\*{0,2}(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)", m.group(0), re.IGNORECASE
        )
        http_method = method_match.group(1).upper() if method_match else None

        # Extract authentication info from nearby text
        auth = _extract_auth_nearby(section, m.start())

        entry_points.append(
            EntryPoint(
                func_block_id=f"{file_path}:{func_name}",
                entry_type="http_route",
                route=route_path,
                http_method=http_method,
                confidence=0.60,
                evidence=f"LLM discovered: {func_ref}",
                needs_llm_review=False,
                authentication=auth,
                source="llm_pre_recon",
            )
        )

    # Pattern 2: Webhook entries
    webhook_pattern = re.compile(
        r"\*{0,2}\bWebhook:?\s+([^\n*]+?)\*{0,2}\s*[—\-–]\s*`([^`]+)`",
        re.IGNORECASE,
    )
    for m in webhook_pattern.finditer(section):
        # Skip if this was already matched as an HTTP route
        line_start = section.rfind("\n", 0, m.start()) + 1
        line_prefix = section[line_start:m.start()].strip()
        if re.match(r"\**(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)", line_prefix, re.IGNORECASE):
            continue

        webhook_path = m.group(1).strip()
        func_ref = m.group(2).strip()
        parts = func_ref.rsplit(":", 1)
        file_path = parts[0] if len(parts) >= 2 else func_ref
        func_name = parts[1] if len(parts) >= 2 else "unknown"

        auth = _extract_auth_nearby(section, m.start())

        entry_points.append(
            EntryPoint(
                func_block_id=f"{file_path}:{func_name}",
                entry_type="webhook",
                route=webhook_path,
                http_method="POST",
                confidence=0.60,
                evidence=f"LLM discovered webhook: {func_ref}",
                needs_llm_review=False,
                authentication=auth,
                source="llm_pre_recon",
            )
        )

    return entry_points


def _extract_auth_nearby(text: str, position: int, window: int = 120) -> str | None:
    """Look for authentication keywords near a match position."""
    nearby = text[position : min(len(text), position + window)].lower()

    if "authentication: public" in nearby or "auth: public" in nearby:
        return "public"
    if "authentication: required" in nearby or "auth: required" in nearby:
        return "required"
    if "authentication" in nearby:
        return "unknown"
    return None
