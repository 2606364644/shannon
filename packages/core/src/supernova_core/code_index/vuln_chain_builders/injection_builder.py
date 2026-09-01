"""injection GitNexus-track builder (spec §5.4, forward source->sink).

Takes candidate chains (Task 3, forward direction) for injection sinks
(SQL/command/file/template/deserialize), runs the light LLM chain-verdict
pass, and emits InjectionVulnerability records for the GitNexus-track queue.
"""

import asyncio
import logging
from typing import Awaitable, Callable

from supernova_core.code_index.verdict_checkpoint import VerdictCheckpoint
from supernova_core.code_index.chain_verdict import (
    extract_candidate_chains,
    gather_verdicts_concurrently,
    http_route_label,
    placement_noted_params,
)
from supernova_core.code_index.models import EntryPoint, ParameterSource
from supernova_core.code_index.parameter_models import ParameterPropagationGraph, SinkCallSite
from supernova_core.code_index.progress import ProgressCb, ProgressEmitter
from supernova_core.models.queue_schemas import InjectionVulnerability

logger = logging.getLogger(__name__)

# SlotContext -> injection slot label (vuln-injection.txt:113)
_SLOT_LABEL = {
    "sql_value": "SQL-val",
    "sql_identifier": "SQL-ident",
    "cmd_argument": "CMD-argument",
    "file_path": "FILE-path",
    "template_expr": "TEMPLATE-expression",
    "deserialize": "DESERIALIZE-object",
}


def _source_text(candidate) -> str:
    """Render source as 'param (file:line)' from entry_point_id."""
    return f"{candidate.source_param} ({candidate.entry_point_id})"


async def build_injection_findings(
    pgraph: ParameterPropagationGraph,
    *,
    verdict_agent: Callable[..., Awaitable] | None = None,
    sink_call_sites: dict[str, SinkCallSite] | None = None,
    progress_cb: ProgressCb = None,
    entry_points: dict[str, EntryPoint] | None = None,
    semaphore: "asyncio.Semaphore | None" = None,
    verdict_checkpoint: "VerdictCheckpoint | None" = None,
) -> list[InjectionVulnerability]:
    candidates = extract_candidate_chains(
        pgraph, vuln_class="injection", sink_call_sites=sink_call_sites,
    )
    # STORAGE-sourced chains are the second-order builder's domain (2ND-GN-*
    # findings); single-hop builders must not also emit them (avoids duplicate
    # findings + double LLM cost). Gap A fix.
    candidates = [c for c in candidates
                  if c.source_type != ParameterSource.STORAGE.value]
    emitter = ProgressEmitter("chain-verdict", len(candidates), progress_cb)

    def _detail(i, chain, verdict):
        if verdict.verdict == "vulnerable":
            return (f"INJ-GN-{i:02d} vulnerable: source={_source_text(chain)} "
                    f"→ sink={chain.sink_call_site_id}")
        return None

    # 逐链并行研判（Semaphore 并发 + gather 保序；预算/agent_name/tick 语义不变）。
    # 护栏（spec 2026-08-27 §3）：超限链 unadjudicated 保守进 findings（helper 内）。
    verdicts = await gather_verdicts_concurrently(
        candidates, vc="injection",
        verdict_agent=verdict_agent, emitter=emitter, detail_of=_detail,
        semaphore=semaphore, checkpoint=verdict_checkpoint)
    findings: list[InjectionVulnerability] = []
    for i, (chain, verdict) in enumerate(zip(candidates, verdicts), start=1):
        concat_note = ""
        if chain.post_sanitize_concat:
            concat_note = "⚠️ post-sanitize concat detected — sanitizer considered ineffective"
        # O2 前半：join entry_point 路由，path 带 "METHOD /path" 前缀（PoC 模板层
        # derive_method_path 直接命中，省一次 gap-fill LLM）。join miss → 原样。
        route_label = http_route_label(chain.entry_point_id, entry_points)
        path = (f"{route_label} → {verdict.evidence_chain}"
                if route_label else verdict.evidence_chain)
        findings.append(InjectionVulnerability(
            ID=f"INJ-GN-{i:02d}",
            vulnerability_type="injection",
            externally_exploitable=(verdict.verdict == "vulnerable"),
            confidence=verdict.confidence,
            title=verdict.title,
            source=_source_text(chain),
            # placement 分层：Agent 判定（verdict.source_param_location）优先，
            # 失败/缺失退 source_type 确定性注记（chain_verdict 通道失败时
            # 仍有位置——PoC 参数位不再依赖文本启发式）。
            affected_parameters=placement_noted_params(chain, verdict),
            path=path,
            sink_call=chain.sink_call_site_id,
            slot_type=_SLOT_LABEL.get(chain.sink_slot, chain.sink_slot),
            concat_occurrences=concat_note or None,
            verdict=verdict.verdict,
            mismatch_reason=verdict.mismatch_reason,
            witness_payload=verdict.witness_payload,
            source_track="gitnexus",
            evidence_chain=verdict.evidence_chain,
            flow_id=chain.flow_id,
            sanitizer_annotations=chain.sanitizer_annotations,
        ))
    await emitter.finalize(
        f"{len(findings)} vulnerable · {len(candidates)} candidates judged")
    logger.info("injection gitnexus-track: %d candidate chains → %d findings",
                len(candidates), len(findings))
    return findings
