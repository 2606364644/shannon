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
from shannon_core.code_index.sink_discovery_llm import (
    RuleGap,
    collect_suspicious_calls,
    discover_sinks_llm,
)
from shannon_core.code_index.source_detector import detect_sources
from shannon_core.code_index.source_discovery_llm import (
    collect_source_candidates,
    discover_sources_llm,
)

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
    auto_index: bool = False,
) -> tuple[CodeIndex, list[RuleGap]]:
    """Build code index with GitNexus call graph + LLM taint analysis.

    Pipeline:
    1. Tree-sitter parse → FuncBlock[]
    2. GitNexus MCP → precise call graph (edges, chains, entry_points)
    3. sink_detector → SinkCallSite[]
    4. LLM taint analysis (per-function, only for functions with sinks)
    5. Deterministic chain propagation (cross-function parameter mapping)

    Args:
        repo_path: Absolute path to the repository root.
        mcp_client: GitNexus MCP client for call graph queries.
        llm_client: LLM client for taint analysis.
        auto_index: If True, attempt to ensure GitNexus has indexed the repo
            via the CLI engine before proceeding. Raises PentestError if
            GitNexus CLI is unavailable or indexing fails (no fallback).

    Raises:
        PentestError: if GitNexus CLI is unavailable, indexing fails, or (later) MCP query fails.
        GitNexusNotIndexedError: if GitNexus hasn't indexed the repo
        GitNexusConnectionError: if MCP connection fails
    """
    from shannon_core.models.errors import ErrorCode, PentestError

    repo = Path(repo_path).resolve()
    file_manifest = discover_security_files(repo)

    # ⓪ Auto-indexing: ensure GitNexus has indexed the repo
    if auto_index:
        from shannon_core.code_index.gitnexus_engine import GitNexusEngine
        engine = GitNexusEngine(repo)
        if not engine.is_available():
            raise PentestError(
                "GitNexus CLI not installed but is required for code indexing. "
                "Install with: npm install -g gitnexus",
                category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
            )
        index_result = engine.ensure_indexed()
        if not index_result.success:
            raise PentestError(
                f"GitNexus indexing failed: {index_result.error_message}. "
                "Code index requires a working GitNexus index.",
                category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
            )

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

    # ③ sink detection (规则)
    def _provide_source(block):
        return file_sources.get(block.file_path)
    sink_call_sites = detect_sinks(all_blocks, parser, source_provider=_provide_source)
    logger.info("Detected %d rule-based sink call sites", len(sink_call_sites))

    # ③b LLM sink 补召回 (spec §3.1): 规则未命中的可疑 call → 软 SinkCallSite
    suspicious = collect_suspicious_calls(all_blocks, parser, source_provider=_provide_source)
    soft_sinks, rule_gaps = await discover_sinks_llm(suspicious, llm_client)
    if soft_sinks:
        sink_call_sites = sink_call_sites + soft_sinks
        logger.info("LLM sink discovery added %d soft sinks (%d rule gaps)",
                    len(soft_sinks), len(rule_gaps))

    # ④ Group sinks by function (含软 sink)
    from collections import defaultdict
    sinks_by_func: dict[str, list] = defaultdict(list)
    for s in sink_call_sites:
        sinks_by_func[s.caller_id].append(s)

    # ⑤ LLM taint analysis (only for functions with sinks)
    blocks_by_id = {b.id: b for b in all_blocks}

    # ⑤ LLM taint analysis (only for functions with sinks) — 并发(治本 2):
    # 串行 per-function LLM 会被 ③b 产的 soft sinks 放大,拖垮 activity 超时。
    async def _taint_one(item):
        func_id, func_sinks = item
        block = blocks_by_id.get(func_id)
        if block is None:
            return None
        result = await analyze_taint_llm(
            block=block,
            sinks_in_func=func_sinks,
            llm_client=llm_client,
        )
        return (func_id, result)

    from shannon_core.code_index.llm_concurrency import map_llm_with_bounds
    from shannon_core.config.concurrency import get_max_concurrent
    taint_pairs = await map_llm_with_bounds(
        list(sinks_by_func.items()), _taint_one,
        concurrency=get_max_concurrent(),
        label="analyze_taint_llm",
    )
    intra_results = {func_id: result for func_id, result in taint_pairs}

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

    # ⑦ entry 组装：detect_entry_points ∪ process entry（G2）
    #    process entry = call_graph.entry_points(path[0] FuncBlock) 中 detect 未识别的，
    #    entry_type="gitnexus_process"（SRPC/RPC 业务入口，非 HTTP）；同 id 时 detect 优先
    #    （保留其 route/http_method）。替代旧的 detect ∩ gitnexus（intersect）。
    all_entry_points = detect_entry_points(all_blocks, language, repo_path=str(repo))
    detected_ids = {ep.func_block_id for ep in all_entry_points}
    process_entries: list[EntryPoint] = []
    for ep_block in call_graph.entry_points:
        if ep_block.id not in detected_ids:
            process_entries.append(EntryPoint(
                func_block_id=ep_block.id,
                entry_type="gitnexus_process",
                route=None,
                http_method=None,
                confidence=0.9,
                evidence=f"GitNexus process entry: {ep_block.function_name}",
                needs_llm_review=False,
                source="gitnexus",
            ))
    gitnexus_entry_points = list(all_entry_points) + process_entries
    logger.info(
        "entry assembly: %d detect + %d gitnexus_process = %d total",
        len(all_entry_points), len(process_entries), len(gitnexus_entry_points),
    )

    # ⑧b source detection（平行 ③ sink detect，独立不依赖 sink）
    entry_point_ids = {ep.func_block_id for ep in gitnexus_entry_points}
    source_points = detect_sources(
        all_blocks, parser, entry_point_ids, source_provider=_provide_source,
    )
    logger.info("Detected %d rule-based source points", len(source_points))

    # ⑧b-LLM source 补召回：规则未命中的 entry handler → 软 SourcePoint
    source_candidates = collect_source_candidates(
        all_blocks, entry_point_ids, source_provider=_provide_source,
    )
    soft_sources = await discover_sources_llm(source_candidates, llm_client)
    if soft_sources:
        source_points = source_points + soft_sources
        logger.info("LLM source discovery added %d soft sources", len(soft_sources))

    # ⑨ Assemble CodeIndex
    return (
        CodeIndex(
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
            source_points=source_points,
            file_manifest=file_manifest,
            degradation_level=DegradationLevel.FULL,
            parameter_graph=pgraph,
        ),
        rule_gaps,
    )


def write_index_files(
    index: CodeIndex,
    output_dir: str,
    *,
    rule_gaps: list | None = None,
) -> tuple[Path, Path]:
    """Write code_index.json, code_index_summary.md, parameter_graph.json,
    and (if any) rule_gap_report.json."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "code_index.json"
    json_path.write_text(index.model_dump_json(indent=2))

    summary_path = out / "code_index_summary.md"
    summary_path.write_text(generate_summary(index))

    pgraph_path = out / "parameter_graph.json"
    if index.parameter_graph is not None:
        pgraph_path.write_text(index.parameter_graph.model_dump_json(indent=2))
    elif pgraph_path.exists():
        pgraph_path.unlink()

    # 旁路: 规则缺口报告(spec §3.1 层 2, 驱动规则库迭代, 不参与 taint/verdict)
    gap_path = out / "rule_gap_report.json"
    if rule_gaps:
        import json as _json
        gap_path.write_text(_json.dumps(
            [g if isinstance(g, dict) else g.__dict__ for g in rule_gaps],
            indent=2, ensure_ascii=False,
        ))
    elif gap_path.exists():
        gap_path.unlink()

    return json_path, summary_path


def run_entry_point_fusion(deliverables_dir: str) -> CodeIndex:
    """Merge deterministic entry points with LLM-discovered entry points.

    Reads code_index.json and pre_recon_deliverable.md, parses LLM entry
    points from the deliverable, merges with deterministic entry points,
    and updates code_index.json in place.

    Args:
        deliverables_dir: Path to the deliverables directory containing
            code_index.json and pre_recon_deliverable.md.

    Returns:
        Updated CodeIndex with merged entry points.
    """
    from shannon_core.code_index.entry_point_fusion import parse_llm_entry_points

    out = Path(deliverables_dir)
    code_index_path = out / "code_index.json"
    deliverable_path = out / "pre_recon_deliverable.md"

    if not code_index_path.exists():
        logger.warning("code_index.json not found; skipping entry point fusion")
        raise FileNotFoundError(f"code_index.json not found in {deliverables_dir}")

    index = CodeIndex.model_validate_json(code_index_path.read_text())

    # Parse LLM entry points from deliverable (if it exists)
    llm_eps: list[EntryPoint] = []
    if deliverable_path.exists():
        deliverable_text = deliverable_path.read_text()
        llm_eps = parse_llm_entry_points(deliverable_text)
        logger.info("Parsed %d LLM entry points from deliverable", len(llm_eps))
    else:
        logger.info("No pre_recon_deliverable.md found; LLM fusion skipped")

    # Merge: deterministic entry points as the base, LLM as supplementary
    deterministic_ids = {ep.func_block_id for ep in index.entry_points}

    # Add LLM-only discoveries
    merged_entries = list(index.entry_points)
    added = 0
    for ep in llm_eps:
        if ep.func_block_id not in deterministic_ids:
            merged_entries.append(ep)
            added += 1
        else:
            logger.debug("LLM entry point %s already in deterministic results, skipping", ep.func_block_id)

    logger.info(
        "Entry point fusion: %d deterministic + %d LLM-only = %d total",
        len(index.entry_points), added, len(merged_entries),
    )

    # Update index
    updated = index.model_copy(update={
        "entry_points": merged_entries,
        "total_entry_points": len(merged_entries),
    })

    # Write updated code_index.json
    code_index_path.write_text(updated.model_dump_json(indent=2))

    return updated


def save_adjudication(deliverables_dir: str) -> None:
    """Adjudicate entry points by confidence and write adjudication result.

    Reads code_index.json, assigns verdict based on confidence thresholds:
    - confidence >= 0.85: CONFIRMED
    - confidence < 0.50: REJECTED
    - otherwise: NEEDS_REVIEW
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
        if ep.confidence >= 0.85:
            verdict = Verdict.CONFIRMED
        elif ep.confidence < 0.50:
            verdict = Verdict.REJECTED
        else:
            verdict = Verdict.NEEDS_REVIEW

        adjudicated.append(AdjudicatedEntryPoint(
            func_block_id=ep.func_block_id,
            verdict=verdict,
            entry_type=ep.entry_type,
            route=ep.route,
            http_method=ep.http_method,
            evidence=ep.evidence,
            source=EntryPointSource.CODE_INDEX if ep.source in ("code_index", "gitnexus")
                    else EntryPointSource.LLM_DISCOVERY,
        ))

    result = AdjudicationResult(
        repository=index.repository,
        language=index.language,
        adjudicated_entry_points=adjudicated,
    )

    entry_points_path = out / "entry_points.json"
    entry_points_path.write_text(result.model_dump_json(indent=2))

    confirmed = sum(1 for a in adjudicated if a.verdict == Verdict.CONFIRMED)
    needs_review = sum(1 for a in adjudicated if a.verdict == Verdict.NEEDS_REVIEW)
    rejected = sum(1 for a in adjudicated if a.verdict == Verdict.REJECTED)
    logger.info(
        "Adjudicated %d entry points: %d confirmed, %d needs_review, %d rejected",
        len(adjudicated), confirmed, needs_review, rejected,
    )
