"""Code index and call graph construction for Shannon's whitebox pipeline."""

import json
import logging
from pathlib import Path

from shannon_core.code_index.models import CodeIndex
from shannon_core.code_index.models import AdjudicationResult, Verdict, EntryPointSource
from shannon_core.code_index.parser import detect_language, discover_source_files
from shannon_core.code_index.call_graph import resolve_edges, build_call_chains
from shannon_core.code_index.entry_points import detect_entry_points
from shannon_core.code_index.summary import generate_summary
from shannon_core.code_index.parsers import get_parser
from shannon_core.code_index.degradation import build_degradation_report
from shannon_core.code_index.file_discovery import discover_security_files
from shannon_core.code_index.gitnexus_engine import GitNexusEngine
from shannon_core.code_index.models import DegradationLevel, FileManifest

logger = logging.getLogger(__name__)


def build_code_index(repo_path: str) -> CodeIndex:
    """Build a complete code index for the repository."""
    from shannon_core.models.errors import ErrorCode, PentestError

    repo = Path(repo_path).resolve()
    try:
        language = detect_language(repo)
    except ValueError as exc:
        raise PentestError(
            str(exc),
            category="code_index",
            error_code=ErrorCode.CODE_INDEX_FAILED,
        ) from exc

    logger.info("Detected language: %s", language)

    source_files = discover_source_files(repo, language)
    if not source_files:
        raise PentestError(
            f"No source files found for language '{language}' in {repo}",
            category="code_index",
            error_code=ErrorCode.CODE_INDEX_FAILED,
        )

    parser = get_parser(language)
    if parser is None:
        raise PentestError(
            f"No parser available for language '{language}'",
            category="code_index",
            error_code=ErrorCode.CODE_INDEX_FAILED,
        )

    all_blocks = []
    all_edges = []
    for file_path in source_files:
        try:
            blocks = parser.parse_file(file_path, repo)
            all_blocks.extend(blocks)

            source = file_path.read_bytes()
            for block in blocks:
                edges = parser.extract_calls(block, source)
                all_edges.extend(edges)
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", file_path, exc)
            continue

    resolved_edges = resolve_edges(all_edges, all_blocks)

    entry_points = detect_entry_points(all_blocks, language, repo_path=str(repo))

    return CodeIndex(
        repository=str(repo),
        language=language,
        total_blocks=len(all_blocks),
        total_entry_points=len(entry_points),
        total_chains=0,
        blocks=all_blocks,
        edges=resolved_edges,
        entry_points=entry_points,
        chains=[],
    )


def build_code_index_with_gitnexus(repo_path: str) -> CodeIndex:
    """Build code index with GitNexus-first strategy and graceful degradation.

    Strategy:
    1. Try GitNexus (CLI + MCP) for full indexing
    2. If unavailable, fall back to existing AST BFS parser
    3. Always discover security files (templates, configs, schemas)
    4. Report degradation level and coverage gaps

    Returns:
        CodeIndex with optional file_manifest and degradation_level attributes.
    """
    repo = Path(repo_path).resolve()

    # Always discover security files regardless of GitNexus availability
    file_manifest = discover_security_files(repo)

    # Try GitNexus
    engine = GitNexusEngine(repo)
    degradation_level = DegradationLevel.FULL

    if engine.is_available():
        try:
            engine.ensure_indexed()
            logger.info("GitNexus indexing successful, using FULL mode")
            # GitNexus extract flow would go here (Plan A integration)
            # For now, fall through to AST with FULL degradation level
            # Full GitNexus extraction will be added in subsequent PRs
            base_index = build_code_index(repo_path)
            # Create a new index with extended fields
            index = CodeIndex(
                repository=base_index.repository,
                language=base_index.language,
                total_blocks=base_index.total_blocks,
                total_entry_points=base_index.total_entry_points,
                total_chains=base_index.total_chains,
                blocks=base_index.blocks,
                edges=base_index.edges,
                entry_points=base_index.entry_points,
                chains=base_index.chains,
                file_manifest=file_manifest,
                degradation_level=degradation_level,
            )
            return index
        except Exception as exc:
            logger.warning("GitNexus failed: %s. Falling back to AST BFS", exc)
            degradation_level = DegradationLevel.DEGRADED
    else:
        logger.info("GitNexus not available, using AST BFS mode")
        degradation_level = DegradationLevel.DEGRADED

    # Fallback: existing AST BFS parser
    base_index = build_code_index(repo_path)
    index = CodeIndex(
        repository=base_index.repository,
        language=base_index.language,
        total_blocks=base_index.total_blocks,
        total_entry_points=base_index.total_entry_points,
        total_chains=base_index.total_chains,
        blocks=base_index.blocks,
        edges=base_index.edges,
        entry_points=base_index.entry_points,
        chains=base_index.chains,
        file_manifest=file_manifest,
        degradation_level=degradation_level,
    )

    # Write degradation report if not FULL
    if degradation_level != DegradationLevel.FULL:
        report = build_degradation_report(degradation_level)
        report_path = repo / "degradation_report.json"
        try:
            report_path.write_text(report.to_json())
            logger.warning("DEGRADED MODE — Coverage gaps: %s", report_path)
        except Exception:
            logger.warning("Could not write degradation report")

    return index


def write_index_files(index: CodeIndex, output_dir: str) -> tuple[Path, Path]:
    """Write code_index.json and code_index_summary.md to output_dir."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "code_index.json"
    json_path.write_text(index.model_dump_json(indent=2))

    summary_path = out / "code_index_summary.md"
    summary_path.write_text(generate_summary(index))

    return json_path, summary_path


def rebuild_call_chains(deliverables_dir: str) -> CodeIndex:
    """Rebuild call chains from adjudicated entry points.

    Reads entry_points.json for confirmed entry point IDs, reads
    code_index.json for blocks/edges, calls build_call_chains() with
    confirmed IDs only, and writes updated code_index.json.
    """
    from shannon_core.models.errors import ErrorCode, PentestError

    out = Path(deliverables_dir)

    code_index_path = out / "code_index.json"
    entry_points_path = out / "entry_points.json"

    if not code_index_path.exists():
        raise PentestError(
            f"code_index.json not found in {deliverables_dir}",
            category="code_index",
            error_code=ErrorCode.CODE_INDEX_FAILED,
        )

    index = CodeIndex.model_validate_json(code_index_path.read_text())

    if not entry_points_path.exists():
        logger.warning("entry_points.json not found; skipping chain rebuild")
        return index

    adjudication = AdjudicationResult.model_validate_json(entry_points_path.read_text())

    block_id_set = {b.id for b in index.blocks}
    confirmed_ids: list[str] = []
    for ep in adjudication.adjudicated_entry_points:
        if ep.verdict != Verdict.CONFIRMED:
            continue
        if ep.func_block_id not in block_id_set:
            if ep.source == EntryPointSource.LLM_DISCOVERY:
                logger.warning(
                    "Unresolved LLM-discovered entry point: %s", ep.func_block_id
                )
            continue
        confirmed_ids.append(ep.func_block_id)

    chains = build_call_chains(confirmed_ids, index.edges, blocks=index.blocks)

    updated = CodeIndex(
        repository=index.repository,
        language=index.language,
        total_blocks=index.total_blocks,
        total_entry_points=index.total_entry_points,
        total_chains=len(chains),
        blocks=index.blocks,
        edges=index.edges,
        entry_points=index.entry_points,
        chains=chains,
        file_manifest=index.file_manifest,
        degradation_level=index.degradation_level,
    )

    code_index_path.write_text(updated.model_dump_json(indent=2))
    summary_path = out / "code_index_summary.md"
    summary_path.write_text(generate_summary(updated))

    logger.info(
        "Rebuilt %d call chains from %d confirmed entry points",
        len(chains),
        len(confirmed_ids),
    )

    return updated
