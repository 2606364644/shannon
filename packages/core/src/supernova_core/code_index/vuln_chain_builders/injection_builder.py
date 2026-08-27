"""injection GitNexus-track builder (spec §5.4, forward source->sink).

Takes candidate chains (Task 3, forward direction) for injection sinks
(SQL/command/file/template/deserialize), runs the light LLM chain-verdict
pass, and emits InjectionVulnerability records for the GitNexus-track queue.
"""

import logging
from typing import Awaitable, Callable

from supernova_core.code_index.chain_verdict import (
    extract_candidate_chains,
    http_route_label,
    judge_chain_verdict,
    placement_noted_params,
    unadjudicated_verdict,
)
from supernova_core.config.concurrency import get_chain_verdict_max_agents
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
    llm_client: Callable[..., Awaitable[str]] | None = None,
    verdict_agent: Callable[..., Awaitable] | None = None,
    sink_call_sites: dict[str, SinkCallSite] | None = None,
    progress_cb: ProgressCb = None,
    entry_points: dict[str, EntryPoint] | None = None,
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
    # 护栏（spec 2026-08-27 §3）：逐条多轮深判的链数上限——超限链不再跑
    # agent，unadjudicated 保守进 findings（不烧 token、不静默丢）。
    max_agents = get_chain_verdict_max_agents()
    findings: list[InjectionVulnerability] = []
    for i, chain in enumerate(candidates, start=1):
        if i > max_agents:
            logger.warning(
                "chain-verdict budget exceeded (%d); candidate %d of %d "
                "left unadjudicated (conservative, in queue)",
                max_agents, i, len(candidates))
            verdict = unadjudicated_verdict(
                chain, f"candidate chain beyond verdict budget ({max_agents}); "
                       f"left unadjudicated for human review")
        else:
            verdict = await judge_chain_verdict(
                chain,
                llm_client=llm_client,
                verdict_agent=verdict_agent,
                agent_name=f"chain-verdict-injection-{i:02d}",
            )
        is_vuln = (verdict.verdict == "vulnerable")
        detail = None
        if is_vuln:
            detail = (f"INJ-GN-{i:02d} vulnerable: source={_source_text(chain)} "
                      f"→ sink={chain.sink_call_site_id}")
        await emitter.tick(detail=detail, hits_delta=1 if is_vuln else 0)
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
