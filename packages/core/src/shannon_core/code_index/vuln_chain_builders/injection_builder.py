"""injection GitNexus-track builder (spec §5.4, forward source->sink).

Takes candidate chains (Task 3, forward direction) for injection sinks
(SQL/command/file/template/deserialize), runs the light LLM chain-verdict
pass, and emits InjectionVulnerability records for the GitNexus-track queue.
"""

import logging
from typing import Awaitable, Callable

from shannon_core.code_index.chain_verdict import (
    extract_candidate_chains,
    judge_chain_verdict,
)
from shannon_core.code_index.parameter_models import ParameterPropagationGraph
from shannon_core.code_index.progress import ProgressCb, ProgressEmitter
from shannon_core.models.queue_schemas import InjectionVulnerability

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
    llm_client: Callable[..., Awaitable[str]],
    progress_cb: ProgressCb = None,
) -> list[InjectionVulnerability]:
    candidates = extract_candidate_chains(pgraph, vuln_class="injection")
    emitter = ProgressEmitter("chain-verdict", len(candidates), progress_cb)
    findings: list[InjectionVulnerability] = []
    for i, chain in enumerate(candidates, start=1):
        verdict = await judge_chain_verdict(chain, llm_client=llm_client)
        is_vuln = (verdict.verdict == "vulnerable")
        detail = None
        if is_vuln:
            detail = (f"INJ-GN-{i:02d} vulnerable: source={_source_text(chain)} "
                      f"→ sink={chain.sink_call_site_id}")
        await emitter.tick(detail=detail, hits_delta=1 if is_vuln else 0)
        concat_note = ""
        if chain.post_sanitize_concat:
            concat_note = "⚠️ post-sanitize concat detected — sanitizer considered ineffective"
        findings.append(InjectionVulnerability(
            ID=f"INJ-GN-{i:02d}",
            vulnerability_type="injection",
            externally_exploitable=(verdict.verdict == "vulnerable"),
            confidence=verdict.confidence,
            source=_source_text(chain),
            path=verdict.evidence_chain,
            sink_call=chain.sink_call_site_id,
            slot_type=_SLOT_LABEL.get(chain.sink_slot, chain.sink_slot),
            concat_occurrences=concat_note or None,
            verdict=verdict.verdict,
            mismatch_reason=verdict.mismatch_reason,
            witness_payload=verdict.witness_payload,
            source_track="gitnexus",
            evidence_chain=verdict.evidence_chain,
        ))
    await emitter.finalize(
        f"{len(findings)} vulnerable · {len(candidates)} candidates judged")
    logger.info("injection gitnexus-track: %d candidate chains → %d findings",
                len(candidates), len(findings))
    return findings
