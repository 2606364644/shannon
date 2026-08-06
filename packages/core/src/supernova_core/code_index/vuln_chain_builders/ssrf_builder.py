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
    judge_chain_verdict,
)
from supernova_core.code_index.models import ParameterSource
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
) -> list[SsrfVulnerability]:
    candidates = extract_candidate_chains(
        pgraph, vuln_class="ssrf", sink_call_sites=sink_call_sites,
    )
    # STORAGE-sourced chains are the second-order builder's domain (2ND-GN-*
    # findings); single-hop builders must not also emit them (avoids duplicate
    # findings + double LLM cost). Gap A fix.
    candidates = [c for c in candidates
                  if c.source_type != ParameterSource.STORAGE.value]
    # Open-redirect sinks (category=REDIRECT, e.g. res.redirect(url)) are a
    # browser-side 302 (OWASP A10), NOT a server-side request forgery. Their url
    # slot matches _SSRF_SLOTS so they'd otherwise be mislabeled URL_Manipulation,
    # polluting the ssrf bucket and breaking dual-track dedup (LLM track labels
    # these Open_Redirect -> merger dedup key differs -> duplicate finding).
    if sink_call_sites:
        candidates = [
            c for c in candidates
            if not (scs := sink_call_sites.get(c.sink_call_site_id))
            or scs.category != SinkCategory.REDIRECT
        ]
    emitter = ProgressEmitter("chain-verdict", len(candidates), progress_cb)
    findings: list[SsrfVulnerability] = []
    for i, chain in enumerate(candidates, start=1):
        verdict = await judge_chain_verdict(chain, llm_client=llm_client)
        is_vuln = (verdict.verdict == "vulnerable")
        detail = None
        if is_vuln:
            detail = (f"SSRF-GN-{i:02d} vulnerable: source={chain.source_param} "
                      f"({chain.entry_point_id}) → sink={chain.sink_call_site_id}")
        await emitter.tick(detail=detail, hits_delta=1 if is_vuln else 0)
        findings.append(SsrfVulnerability(
            ID=f"SSRF-GN-{i:02d}",
            vulnerability_type="URL_Manipulation",
            externally_exploitable=(verdict.verdict == "vulnerable"),
            confidence=verdict.confidence,
            title=verdict.title,
            source_endpoint=chain.entry_point_id,  # best-effort; renderer tolerant
            vulnerable_parameter=chain.source_param,
            vulnerable_code_location=chain.sink_call_site_id,
            missing_defense=verdict.mismatch_reason,
            exploitation_hypothesis=None,
            suggested_exploit_technique=None,
            # Task 2 fields:
            path=verdict.evidence_chain,
            verdict=verdict.verdict,
            witness_payload=verdict.witness_payload,
            source_track="gitnexus",
            evidence_chain=verdict.evidence_chain,
        ))
    await emitter.finalize(
        f"{len(findings)} vulnerable · {len(candidates)} candidates judged")
    logger.info("ssrf gitnexus-track: %d candidates → %d findings",
                len(candidates), len(findings))
    return findings
