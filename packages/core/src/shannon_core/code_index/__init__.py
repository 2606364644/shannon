"""Code index and call graph construction for Shannon's whitebox pipeline."""
import asyncio
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
from shannon_core.code_index.chain_propagator import (
    merge_taint_flows,
    produce_intra_first_taint_flows,
    propagate_across_chains,
    propagate_backward_across_chains,
)
from shannon_core.code_index.parameter_models import ParameterPropagationGraph
from shannon_core.code_index.progress import ProgressEmitter
from shannon_core.code_index.sink_discovery_llm import (
    RuleGap,
    collect_entry_handler_blocks,
    collect_suspicious_calls,
    discover_sinks_by_entry,
    discover_sinks_llm,
)
from shannon_core.code_index.source_detector import detect_sources
from shannon_core.code_index.source_detector import _dedup as _dedup_source_points
from shannon_core.code_index.source_discovery_llm import (
    SourceGap,
    collect_source_candidates,
    discover_sources_by_rules,
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


def _parse_and_detect_sync(repo, language, parser):
    """同步 tree-sitter 解析 + sink/source 候选检测（CPU/FS bound 重活）。

    从 build_code_index_with_gitnexus 抽出，供 asyncio.to_thread 移出 event loop：给
    cancel 一个 await 注入点，避免大仓全量解析阻塞 worker loop 导致 Ctrl+C 不可取消
    （与 run_code_index 的 ensure_indexed_async 同一治本方向）。返回下游 await 段所需
    的 (file_sources, all_blocks, sink_call_sites, suspicious)。
    """
    from shannon_core.models.errors import ErrorCode, PentestError

    source_files = discover_source_files(repo, language)
    if not source_files:
        raise PentestError(
            f"No source files found for language '{language}' in {repo}",
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

    def _provide_source(block):
        return file_sources.get(block.file_path)

    sink_call_sites = detect_sinks(all_blocks, parser, source_provider=_provide_source)
    logger.info("Detected %d rule-based sink call sites", len(sink_call_sites))
    suspicious = collect_suspicious_calls(all_blocks, parser, source_provider=_provide_source)
    return file_sources, all_blocks, sink_call_sites, suspicious


async def build_code_index_with_gitnexus(
    repo_path: str,
    *,
    mcp_client,
    llm_client,
    auto_index: bool = False,
    progress_cb=None,
    model: str | None = None,
) -> tuple[CodeIndex, list[RuleGap], list[SourceGap]]:
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
        index_result = await engine.ensure_indexed_async()
        if not index_result.success:
            raise PentestError(
                f"GitNexus indexing failed: {index_result.error_message}. "
                "Code index requires a working GitNexus index.",
                category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
            )

    # ① Tree-sitter parse → FuncBlock[]
    # detect_language / get_parser 留 loop 上（快、早期 PentestError 更清晰）；重 CPU 的
    # 全量解析+检测移进 _parse_and_detect_sync 经 asyncio.to_thread 跑，不阻塞 event loop
    # （给 cancel 一个 await 注入点，治本 Ctrl+C 不可取消）。parser 留作用域供后续 detect_sources 用。
    try:
        language = detect_language(repo)
    except ValueError as exc:
        raise PentestError(
            str(exc), category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
        ) from exc
    logger.info("Detected language: %s", language)

    parser = get_parser(language)
    if parser is None:
        raise PentestError(
            f"No parser available for language '{language}'",
            category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
        )

    file_sources, all_blocks, sink_call_sites, suspicious = await asyncio.to_thread(
        _parse_and_detect_sync, repo, language, parser,
    )

    # 重建 _provide_source 闭包（引用 to_thread 返回的 file_sources，供后续 source 检测用）
    def _provide_source(block):
        return file_sources.get(block.file_path)

    # ② GitNexus MCP → precise call graph
    call_graph = await build_call_graph_from_gitnexus(
        repo_path=str(repo),
        mcp_client=mcp_client,
        blocks=all_blocks,
    )

    # ③b LLM sink 补召回 (spec §3.1): 规则未命中的可疑 call → 软 SinkCallSite
    soft_sinks, rule_gaps = await discover_sinks_llm(
        suspicious, llm_client, progress_cb=progress_cb, model=model)
    if soft_sinks:
        sink_call_sites = sink_call_sites + soft_sinks
        logger.info("LLM sink discovery added %d soft sinks (%d rule gaps)",
                    len(soft_sinks), len(rule_gaps))

    # ⑦ entry 组装(spec 子项① 前置): detect_entry_points ∪ process entry（G2）。
    #    **提前到 taint 之前**(原在 ⑤ 之后)—— sink 探测器(③c)消费 entry_point_ids,
    #    故 entry_point_ids 必须在 ③c 之前算出; taint 仍自由引用 entry_point_ids。
    #    process entry = call_graph.entry_points(path[0] FuncBlock) 中 detect 未识别的，
    #    entry_type="gitnexus_process"(SRPC/RPC 业务入口,非 HTTP);同 id 时 detect 优先
    #    (保留其 route/http_method)。替代旧的 detect ∩ gitnexus(intersect)。
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
    entry_point_ids = {ep.func_block_id for ep in gitnexus_entry_points}

    # ④ Group sinks by function (含软 sink)
    from collections import defaultdict
    sinks_by_func: dict[str, list] = defaultdict(list)
    for s in sink_call_sites:
        sinks_by_func[s.caller_id].append(s)

    # ③c LLM sink 探测器(spec 子项③): entry handler 内 LLM 自由找 sink,
    #     补判定器(候选表筛选)漏的框架特有 sink(fastjson.parseObject 等)。
    #     必须在 ⑤ taint analysis 之前: taint 消费 sinks_by_func。
    sink_func_ids_prelim = set(sinks_by_func.keys())  # 规则+判定器软 sink 的函数
    entry_handler_cands = collect_entry_handler_blocks(
        all_blocks, entry_point_ids=entry_point_ids,
        sink_func_ids=sink_func_ids_prelim)
    hunter_sinks, _hunter_gaps = await discover_sinks_by_entry(
        entry_handler_cands, llm_client, progress_cb=progress_cb, model=model)
    if hunter_sinks:
        sink_call_sites = sink_call_sites + hunter_sinks
        for s in hunter_sinks:
            sinks_by_func[s.caller_id].append(s)
        logger.info("LLM sink hunter (entry-driven) added %d soft sinks", len(hunter_sinks))

    # ⑤ LLM taint analysis (only for functions with sinks)
    blocks_by_id = {b.id: b for b in all_blocks}

    # ⑤ LLM taint analysis (only for functions with sinks) — 并发(治本 2):
    # 串行 per-function LLM 会被 ③b 产的 soft sinks 放大,拖垮 activity 超时。

    def _count_taint_flows(result) -> int:
        """per-function taint-flow 计数。

        analyze_taint_llm 返回 IntraResult(tainted_params: set, hits: dict,
        local_steps: list)。tainted_params 非空 = 该函数存在 source→sink
        taint，计为 1 个 taint flow（per-function 粒度，与 map 的 item 粒度一致）。
        """
        if result is None:
            return 0
        return 1 if result.tainted_params else 0

    taint_emitter = ProgressEmitter("taint-analysis", len(sinks_by_func), progress_cb)

    async def _taint_one(item):
        func_id, func_sinks = item
        block = blocks_by_id.get(func_id)
        if block is None:
            await taint_emitter.tick(detail=None, hits_delta=0)  # 仍计数保持准确
            return None
        result = await analyze_taint_llm(
            block=block,
            sinks_in_func=func_sinks,
            llm_client=llm_client,
        )
        flows_count = _count_taint_flows(result)
        detail = f"taint flow in {block.function_name}" if flows_count else None
        await taint_emitter.tick(detail=detail, hits_delta=flows_count)
        return (func_id, result)

    from shannon_core.code_index.llm_concurrency import map_llm_with_bounds
    from shannon_core.config.concurrency import get_max_concurrent
    taint_items = list(sinks_by_func.items())

    async def _on_skip(idx, message):
        # idx → 函数名(同 sink/source discovery): per-function taint 超时/错误诊断走
        # dispatcher 通道, 取代撞 footer 的裸 logger.warning。
        func_id = taint_items[idx][0]
        block = blocks_by_id.get(func_id)
        who = block.function_name if block else func_id
        await taint_emitter.note(f"{who}: {message}")

    taint_pairs = await map_llm_with_bounds(
        taint_items, _taint_one,
        concurrency=get_max_concurrent(),
        label="analyze_taint_llm",
        on_skip=_on_skip,
    )
    await taint_emitter.finalize(
        f"{sum(_count_taint_flows(r) for _, r in taint_pairs)} taint_flows")
    intra_results = {func_id: result for func_id, result in taint_pairs}

    # ⑧b source detection（平行 ③ sink detect，独立不依赖 sink；主路径扫 entry_point）
    #    entry_point_ids 已在 ⑦(③b 后)提前算出, 此处直接引用(原 :295 行删除, 解耦子项①)。
    source_points = detect_sources(
        all_blocks, parser, entry_point_ids, source_provider=_provide_source,
    )
    n_entry_sources = len(source_points)
    logger.info("Detected %d rule-based source points (entry_point scan)",
                n_entry_sources)

    # ⑧b' source 补召回(spec 2026-07-10 §3.1):对**含 sink 函数**补 source ——
    #    NodeGoat handler 不在 entry_point(detect_entry_points 把路由归 index.js),
    #    source_detector 主路径漏扫 → 这里对含 sink 函数补回 req.body.preTax 等。
    #    规则路径(扩范围到含 sink 函数)+ LLM 补解构;主路径不变(守"source 不被 sink 驱动")。
    sink_func_ids = set(sinks_by_func.keys())
    rule_extra_sources = discover_sources_by_rules(
        all_blocks, sink_func_ids, source_provider=_provide_source,
        entry_point_ids=entry_point_ids,
    )
    source_candidates = collect_source_candidates(
        all_blocks, sink_func_ids, source_provider=_provide_source,
        entry_point_ids=entry_point_ids,
    )
    soft_sources, source_gaps = await discover_sources_llm(
        source_candidates, llm_client, progress_cb=progress_cb, model=model)
    # 合并去重(按 entry_point_id + param_name + source_type);entry 主路径优先。
    source_points = _dedup_source_points(
        source_points + rule_extra_sources + soft_sources)
    logger.info(
        "source recall: %d entry + %d sink-func rule + %d soft → %d unique "
        "(%d source gaps)",
        n_entry_sources, len(rule_extra_sources), len(soft_sources),
        len(source_points), len(source_gaps),
    )

    # ⑥' propagation:
    #   - intra-first(spec 2026-07-10 §3.2):不依赖 chain,对每个含 sink 函数直接产 flow
    #     (覆盖 handler 不在 chain → backward 丢弃 intra 结果的根因,§2)。
    #   - backward(Sink→Source):沿 chain 反向,终点 SourcePoint 锚定(跨函数)。
    #   合并去重(intra-first 同函数优先,backward 补跨函数)。
    #   注:必须在 ⑧b source detect 之后(消费 source_points 锚定终点)。
    backward_flows = propagate_backward_across_chains(
        chains=call_graph.chains,
        blocks=all_blocks,
        intra_results=intra_results,
        sink_call_sites=sink_call_sites,
        source_points=source_points,
    )
    intra_first_flows = produce_intra_first_taint_flows(
        sink_call_sites=sink_call_sites,
        intra_results=intra_results,
        source_points=source_points,
        blocks=all_blocks,
    )
    taint_flows = merge_taint_flows(intra_first_flows, backward_flows)
    pgraph = ParameterPropagationGraph(
        taint_flows=taint_flows,
        language_coverage=[language],
    )
    logger.info(
        "propagation: %d intra-first + %d backward → %d taint_flows "
        "(source_points=%d, sinks=%d)",
        len(intra_first_flows), len(backward_flows), len(pgraph.taint_flows),
        len(source_points), len(sink_call_sites),
    )

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
        source_gaps,
    )


def write_index_files(
    index: CodeIndex,
    output_dir: str,
    *,
    rule_gaps: list | None = None,
    source_gaps: list | None = None,
) -> tuple[Path, Path]:
    """Write code_index.json, code_index_summary.md, parameter_graph.json,
    and (if any) rule_gap_report.json / source_gap_report.json."""
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

    # 旁路: source 规则缺口报告(spec 2026-07-10 §3.1, 反哺 source_rules.yml)
    src_gap_path = out / "source_gap_report.json"
    if source_gaps:
        import json as _json
        src_gap_path.write_text(_json.dumps(
            [g if isinstance(g, dict) else g.__dict__ for g in source_gaps],
            indent=2, ensure_ascii=False,
        ))
    elif src_gap_path.exists():
        src_gap_path.unlink()

    return json_path, summary_path


def run_entry_point_fusion(
    deliverables_dir: str, repo_path: str | None = None,
) -> CodeIndex:
    """Merge deterministic entry points with LLM- and schema-discovered entry points.

    Sources merged (all dedup by func_block_id, deterministic code_index as base):
    - deterministic (code_index.json entry_points) — base
    - LLM (pre_recon_deliverable.md, parsed) — only if deliverable exists
    - OpenAPI/Swagger schema files (repo_path) — only if repo_path given (G5)

    Args:
        deliverables_dir: Path to the deliverables directory containing
            code_index.json and pre_recon_deliverable.md.
        repo_path: Optional repo root to scan for OpenAPI/Swagger schema
            files. When None (default), no schema scan is performed
            (back-compat with pre-G5 callers).

    Returns:
        Updated CodeIndex with merged entry points.
    """
    from shannon_core.code_index.entry_point_fusion import parse_llm_entry_points
    from shannon_core.code_index.schema_entry_parser import parse_openapi_schema_files

    out = Path(deliverables_dir)
    code_index_path = out / "code_index.json"
    deliverable_path = out / "pre_recon_deliverable.md"

    if not code_index_path.exists():
        logger.warning("code_index.json not found; skipping entry point fusion")
        raise FileNotFoundError(f"code_index.json not found in {deliverables_dir}")

    index = CodeIndex.model_validate_json(code_index_path.read_text())

    # Source: LLM pre-recon (only if deliverable exists)
    llm_eps: list[EntryPoint] = []
    if deliverable_path.exists():
        deliverable_text = deliverable_path.read_text()
        llm_eps = parse_llm_entry_points(deliverable_text)
        logger.info("Parsed %d LLM entry points from deliverable", len(llm_eps))
    else:
        logger.info("No pre_recon_deliverable.md found; LLM fusion skipped")

    # Source: OpenAPI/Swagger schema files (G5; only if repo_path given)
    schema_eps: list[EntryPoint] = []
    if repo_path:
        schema_eps = parse_openapi_schema_files(repo_path)
        logger.info("Parsed %d schema entry points from OpenAPI files", len(schema_eps))

    # Merge: deterministic as base, append LLM- and schema-only discoveries
    deterministic_ids = {ep.func_block_id for ep in index.entry_points}
    merged_entries = list(index.entry_points)
    added_llm = 0
    added_schema = 0
    for ep in llm_eps:
        if ep.func_block_id not in deterministic_ids:
            merged_entries.append(ep)
            added_llm += 1
        else:
            logger.debug("LLM entry point %s already in deterministic results, skipping", ep.func_block_id)
    for ep in schema_eps:
        if ep.func_block_id not in deterministic_ids:
            merged_entries.append(ep)
            added_schema += 1
        else:
            logger.debug("Schema entry point %s already in deterministic results, skipping", ep.func_block_id)

    logger.info(
        "Entry point fusion: %d deterministic + %d LLM-only + %d schema-only = %d total",
        len(index.entry_points), added_llm, added_schema, len(merged_entries),
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
