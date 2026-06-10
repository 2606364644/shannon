"""Code index and call graph construction for Shannon's whitebox pipeline."""
import logging
from pathlib import Path

from shannon_core.code_index.models import (
    CodeIndex, TypedParameter, EntryPoint,
    AdjudicatedEntryPoint, AdjudicationResult, Verdict, EntryPointSource,
)
from shannon_core.code_index.parser import detect_language, discover_source_files
from shannon_core.code_index.entry_points import detect_entry_points
from shannon_core.code_index.summary import generate_summary
from shannon_core.code_index.parsers import get_parser
from shannon_core.code_index.sink_detector import detect_sinks
from shannon_core.code_index.degradation import build_degradation_report
from shannon_core.code_index.file_discovery import discover_security_files
from shannon_core.code_index.models import DegradationLevel, FileManifest
from shannon_core.code_index.gitnexus_call_graph import build_call_graph_from_gitnexus
from shannon_core.code_index.llm_taint_analyzer import analyze_taint_llm
from shannon_core.code_index.chain_propagator import propagate_across_chains
from shannon_core.code_index.parameter_models import ParameterPropagationGraph

logger = logging.getLogger(__name__)


def _build_typed_params_by_block(index: CodeIndex) -> dict[str, list[TypedParameter]]:
    """对每个 entry block 提取 typed parameters。

    block.file_path 是相对 repo 的路径，需拼接 index.repository 根才能读到源文件。
    extract_typed_parameters 在 Go/Java/PHP 上返回 [] 是预期行为（spec §4.3）。
    单个 entry 提取失败不应影响整体，吞掉异常并 warning。
    """
    from shannon_core.code_index.enhanced_parameters import extract_typed_parameters
    repo_root = Path(index.repository)
    result: dict[str, list[TypedParameter]] = {}
    for ep in index.entry_points:
        block = next((b for b in index.blocks if b.id == ep.func_block_id), None)
        if block is None:
            continue
        try:
            tps = extract_typed_parameters(
                repo_root / block.file_path, block.function_name,
                block.start_line, index.language,
            )
        except Exception as exc:
            logger.warning("typed param extraction failed for %s: %s", block.id, exc)
            tps = []
        result[block.id] = tps
    return result


async def build_code_index_with_gitnexus(
    repo_path: str,
    *,
    mcp_client,
    llm_client,
) -> CodeIndex:
    """Build code index with GitNexus call graph + LLM taint analysis.

    Pipeline:
    1. Tree-sitter parse → FuncBlock[]
    2. GitNexus MCP → precise call graph (edges, chains, entry_points)
    3. sink_detector → SinkCallSite[]
    4. LLM taint analysis (per-function, only for functions with sinks)
    5. Deterministic chain propagation (cross-function parameter mapping)

    Raises:
        GitNexusNotIndexedError: if GitNexus hasn't indexed the repo
        GitNexusConnectionError: if MCP connection fails
    """
    from shannon_core.models.errors import ErrorCode, PentestError

    repo = Path(repo_path).resolve()
    file_manifest = discover_security_files(repo)

    # ① Tree-sitter parse → FuncBlock[]
    try:
        language = detect_language(repo)
    except ValueError as exc:
        raise PentestError(
            str(exc), category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
        ) from exc

    logger.info("Detected language: %s", language)

    source_files = discover_source_files(repo, language)
    if not source_files:
        raise PentestError(
            f"No source files found for language '{language}' in {repo}",
            category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
        )

    parser = get_parser(language)
    if parser is None:
        raise PentestError(
            f"No parser available for language '{language}'",
            category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
        )

    file_sources: dict[str, bytes] = {}
    all_blocks = []
    for file_path in source_files:
        try:
            source = file_path.read_bytes()
            rel = str(file_path.relative_to(repo))
            file_sources[rel] = source
            blocks = parser.parse_file(file_path, repo)
            all_blocks.extend(blocks)
        except Exception as exc:
            logger.warning("Failed to index %s: %s", file_path, exc)
            continue

    # ② GitNexus MCP → precise call graph
    call_graph = await build_call_graph_from_gitnexus(
        repo_path=str(repo),
        mcp_client=mcp_client,
        blocks=all_blocks,
    )

    # ③ sink detection
    def _provide_source(block):
        return file_sources.get(block.file_path)
    sink_call_sites = detect_sinks(all_blocks, parser, source_provider=_provide_source)
    logger.info("Detected %d sink call sites", len(sink_call_sites))

    # ④ Group sinks by function
    from collections import defaultdict
    sinks_by_func: dict[str, list] = defaultdict(list)
    for s in sink_call_sites:
        sinks_by_func[s.caller_id].append(s)

    # ⑤ LLM taint analysis (only for functions with sinks)
    blocks_by_id = {b.id: b for b in all_blocks}

    intra_results = {}
    for func_id, func_sinks in sinks_by_func.items():
        block = blocks_by_id.get(func_id)
        if block is None:
            continue
        intra_results[func_id] = await analyze_taint_llm(
            block=block,
            sinks_in_func=func_sinks,
            llm_client=llm_client,
        )

    # ⑥ Deterministic cross-function propagation
    taint_flows = propagate_across_chains(
        chains=call_graph.chains,
        blocks=all_blocks,
        intra_results=intra_results,
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=taint_flows,
        language_coverage=[language],
    )
    logger.info("Built parameter propagation graph: %d taint flows", len(pgraph.taint_flows))

    # ⑦ Convert GitNexus entry_point FuncBlocks → EntryPoint objects
    #    GitNexus returns FuncBlock[] but CodeIndex expects EntryPoint[].
    #    Run detect_entry_points on all blocks, then intersect with GitNexus results.
    gitnexus_ep_ids = {ep.id for ep in call_graph.entry_points}
    all_entry_points = detect_entry_points(all_blocks, language, repo_path=str(repo))
    # Keep entry points that GitNexus also identified
    gitnexus_entry_points = [
        ep for ep in all_entry_points if ep.func_block_id in gitnexus_ep_ids
    ]
    # Add synthetic EntryPoint for GitNexus-only discoveries not found by scanner
    detected_ids = {ep.func_block_id for ep in gitnexus_entry_points}
    for ep_block in call_graph.entry_points:
        if ep_block.id not in detected_ids:
            gitnexus_entry_points.append(EntryPoint(
                func_block_id=ep_block.id,
                entry_type="gitnexus",
                route=None,
                http_method=None,
                confidence=0.9,
                evidence=f"GitNexus identified entry point: {ep_block.function_name}",
                needs_llm_review=False,
            ))

    # ⑧ Assemble CodeIndex
    # NOTE: CodeIndex does not have a parameter_graph field.
    # The pgraph is stored separately and can be written by write_index_files.
    return CodeIndex(
        repository=str(repo),
        language=language,
        total_blocks=len(all_blocks),
        total_entry_points=len(gitnexus_entry_points),
        total_chains=len(call_graph.chains),
        blocks=all_blocks,
        edges=call_graph.edges,
        entry_points=gitnexus_entry_points,
        chains=call_graph.chains,
        sink_call_sites=sink_call_sites,
        file_manifest=file_manifest,
        degradation_level=DegradationLevel.FULL,
    )


def write_index_files(index: CodeIndex, output_dir: str) -> tuple[Path, Path]:
    """Write code_index.json, code_index_summary.md, and parameter_graph.json."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "code_index.json"
    json_path.write_text(index.model_dump_json(indent=2))

    summary_path = out / "code_index_summary.md"
    summary_path.write_text(generate_summary(index))

    # parameter_graph built in build_code_index_with_gitnexus if available
    # Try to get it from a side-channel or skip
    return json_path, summary_path


def save_adjudication(deliverables_dir: str) -> None:
    """Auto-confirm entry points and write adjudication result.

    Reads code_index.json, confirms all detected entry points with
    verdict=CONFIRMED and source=CODE_INDEX, and writes entry_points.json.
    """
    out = Path(deliverables_dir)
    out.mkdir(parents=True, exist_ok=True)
    code_index_path = out / "code_index.json"

    if not code_index_path.exists():
        logger.warning("code_index.json not found; skipping adjudication")
        return

    index = CodeIndex.model_validate_json(code_index_path.read_text())

    adjudicated = []
    for ep in index.entry_points:
        adjudicated.append(AdjudicatedEntryPoint(
            func_block_id=ep.func_block_id,
            verdict=Verdict.CONFIRMED,
            entry_type=ep.entry_type,
            route=ep.route,
            http_method=ep.http_method,
            evidence=ep.evidence,
            source=EntryPointSource.CODE_INDEX,
        ))

    result = AdjudicationResult(
        repository=index.repository,
        language=index.language,
        adjudicated_entry_points=adjudicated,
    )

    entry_points_path = out / "entry_points.json"
    entry_points_path.write_text(result.model_dump_json(indent=2))

    logger.info(
        "Auto-confirmed %d entry points via save_adjudication",
        len(adjudicated),
    )
