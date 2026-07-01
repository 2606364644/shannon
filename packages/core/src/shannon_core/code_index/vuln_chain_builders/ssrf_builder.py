"""ssrf GitNexus-track builder (spec §5.6, backward sink->source).

The 7-step SSRF methodology already lives in the LLM-track prompt
(vuln-ssrf.txt:118-238). This GitNexus-track builder does NOT re-run that
methodology -- it only runs the light backward chain-verdict pass on url-slot
candidate chains and emits SsrfVulnerability records (with the path/verdict/
witness_payload fields added in Task 2) for the merger.
"""

import logging
from typing import Awaitable, Callable

from shannon_core.code_index.chain_verdict import (
    extract_candidate_chains,
    judge_chain_verdict,
)
from shannon_core.code_index.parameter_models import ParameterPropagationGraph
from shannon_core.code_index.progress import ProgressCb, ProgressEmitter
from shannon_core.models.queue_schemas import SsrfVulnerability

logger = logging.getLogger(__name__)


async def build_ssrf_findings(
    pgraph: ParameterPropagationGraph,
    *,
    llm_client: Callable[..., Awaitable[str]],
    progress_cb: ProgressCb = None,
) -> list[SsrfVulnerability]:
    candidates = extract_candidate_chains(pgraph, vuln_class="ssrf")
    emitter = ProgressEmitter("chain-verdict", len(candidates), progress_cb)
    findings: list[SsrfVulnerability] = []
    for i, chain in enumerate(candidates, start=1):
        verdict = await judge_chain_verdict(chain, llm_client=llm_client)
        is_vuln = (verdict.verdict == "vulnerable")
        detail: str | None = None
        if is_vuln:
            detail = (f"SSRF-GN-{i:02d} vulnerable: "
                      f"source={chain.source_param} ({chain.entry_point_id}) "
                      f"→ sink={chain.sink_call_site_id}")
        await emitter.tick(detail=detail, hits_delta=1 if is_vuln else 0)
        findings.append(SsrfVulnerability(
            ID=f"SSRF-GN-{i:02d}",
            vulnerability_type="URL_Manipulation",
            externally_exploitable=(verdict.verdict == "vulnerable"),
            confidence=verdict.confidence,
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
