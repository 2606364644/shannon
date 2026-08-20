"""ssrf GitNexus-track builder (spec §5.6, backward sink->source).

The 7-step SSRF methodology already lives in the LLM-track prompt
(vuln-ssrf.txt:118-238). This GitNexus-track builder does NOT re-run that
methodology -- it only runs the light backward chain-verdict pass on url-slot
candidate chains and emits SsrfVulnerability records (with the path/verdict/
witness_payload fields added in Task 2) for the merger.
"""

import logging
from typing import Awaitable, Callable

from supernova_core.code_index.chain_verdict import (
    extract_candidate_chains,
    http_route_label,
    judge_chain_verdict,
)
from supernova_core.code_index.models import EntryPoint, ParameterSource
from supernova_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    SinkCallSite,
    SinkCategory,
)
from supernova_core.code_index.progress import ProgressCb, ProgressEmitter
from supernova_core.models.queue_schemas import SsrfVulnerability

logger = logging.getLogger(__name__)


async def build_ssrf_findings(
    pgraph: ParameterPropagationGraph,
    *,
    llm_client: Callable[..., Awaitable[str]],
    sink_call_sites: dict[str, SinkCallSite] | None = None,
    progress_cb: ProgressCb = None,
    entry_points: dict[str, EntryPoint] | None = None,
) -> list[SsrfVulnerability]:
    candidates = extract_candidate_chains(
        pgraph, vuln_class="ssrf", sink_call_sites=sink_call_sites,
    )
    # STORAGE-sourced chains are the second-order builder's domain (2ND-GN-*
    # findings); single-hop builders must not also emit them (avoids duplicate
    # findings + double LLM cost). Gap A fix.
    candidates = [c for c in candidates
                  if c.source_type != ParameterSource.STORAGE.value]
    # spec 2026-08-21 修复点 E: REDIRECT sink(原有意过滤)改产 Open_Redirect 子型。
    # 原过滤的开轨假设——LLM 轨会以 Open_Redirect 报、GitNexus 报 URL_Manipulation
    # 会 dedup key 不一致——在关轨时破产(LLM 轨静默 + 本轨丢弃 = 双轨全盲,
    # NodeGoat /learn 漏报)。vuln-ssrf.txt §8/枚举本就含 Open_Redirect(URL 大类),
    # GitNexus 产同枚举后 merger _finding_key(含 vulnerability_type)天然对齐。
    emitter = ProgressEmitter("chain-verdict", len(candidates), progress_cb)
    findings: list[SsrfVulnerability] = []
    for i, chain in enumerate(candidates, start=1):
        verdict = await judge_chain_verdict(chain, llm_client=llm_client)
        is_vuln = (verdict.verdict == "vulnerable")
        scs = (sink_call_sites or {}).get(chain.sink_call_site_id)
        vtype = ("Open_Redirect"
                 if scs is not None and scs.category == SinkCategory.REDIRECT
                 else "URL_Manipulation")
        detail = None
        if is_vuln:
            detail = (f"SSRF-GN-{i:02d} vulnerable: source={chain.source_param} "
                      f"({chain.entry_point_id}) → sink={chain.sink_call_site_id}")
        await emitter.tick(detail=detail, hits_delta=1 if is_vuln else 0)
        # O2 前半：join entry_point 路由。source_endpoint 优先写 "METHOD /path"
        #（原来是 FuncBlock id 占位，对 PoC 层无路由价值）；join miss → 保持占位。
        route_label = http_route_label(chain.entry_point_id, entry_points)
        path = (f"{route_label} → {verdict.evidence_chain}"
                if route_label else verdict.evidence_chain)
        findings.append(SsrfVulnerability(
            ID=f"SSRF-GN-{i:02d}",
            vulnerability_type=vtype,
            externally_exploitable=(verdict.verdict == "vulnerable"),
            confidence=verdict.confidence,
            title=verdict.title,
            source_endpoint=route_label or chain.entry_point_id,  # best-effort; renderer tolerant
            vulnerable_parameter=chain.source_param,
            vulnerable_code_location=chain.sink_call_site_id,
            missing_defense=verdict.mismatch_reason,
            exploitation_hypothesis=None,
            suggested_exploit_technique=None,
            # Task 2 fields:
            path=path,
            verdict=verdict.verdict,
            witness_payload=verdict.witness_payload,
            source_track="gitnexus",
            evidence_chain=verdict.evidence_chain,
            flow_id=chain.flow_id,
            sanitizer_annotations=chain.sanitizer_annotations,
        ))
    await emitter.finalize(
        f"{len(findings)} vulnerable · {len(candidates)} candidates judged")
    logger.info("ssrf gitnexus-track: %d candidates → %d findings",
                len(candidates), len(findings))
    return findings
