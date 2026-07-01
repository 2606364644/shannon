"""xss GitNexus-track builder (spec §5.5, backward sink->source + Stored fill).

Two responsibilities:
1. Backward chain verdict for code-level XSS sinks (SinkCategory.XSS). These
   route via sink_call_sites (read from code_index.json) because SlotContext
   has no render-context member; render_context is derived from sink_subtype.
2. DB read<->write cross-table fill for Stored XSS: when a read flow (DB->render,
   INTERNAL source reaching a render sink) has a matching write flow (user input
   -> same DB field), synthesize a Stored XSS candidate spanning
   input->DB->read->render. This fills the blind spot where vuln-xss.txt:151-156's
   DB Read Checkpoint stops at read. 宁缺勿错拼: unmatched field names are skipped.
"""

import logging
from typing import Awaitable, Callable

from shannon_core.code_index.chain_verdict import (
    CandidateChain,
    extract_candidate_chains,
    judge_chain_verdict,
)
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    SinkCallSite,
    TaintFlow,
)
from shannon_core.code_index.models import ParameterSource
from shannon_core.code_index.progress import ProgressCb, ProgressEmitter
from shannon_core.models.queue_schemas import XssVulnerability

logger = logging.getLogger(__name__)

# render_context (lowercase canonical from chain_verdict) -> output label
_RENDER_CONTEXT = {
    "html_body": "HTML_BODY",
    "html_attribute": "HTML_ATTRIBUTE",
    "javascript_string": "JAVASCRIPT_STRING",
    "url_param": "URL_PARAM",
    "css_value": "CSS_VALUE",
}

# SQL slot values that represent a WRITE (insert/update) sink
_SQL_WRITE_SLOTS = {"sql_value"}

# Slots that are NOT render sinks — a Stored-XSS read flow must reach a render
# (DOM) sink. SlotContext has no render member, so the catch-all "generic" is
# what DOM sinks get; excluding executable/SSRF slots prevents mis-pairing an
# INTERNAL-source flow into a SQL/command sink as "Stored XSS".
_NON_RENDER_SLOTS = {
    "sql_value", "sql_identifier", "cmd_argument", "file_path",
    "template_expr", "deserialize", "url",
}


def _src_value(source) -> str:
    return source.value if hasattr(source, "value") else str(source)


def _slot_value(slot) -> str:
    return slot.value if hasattr(slot, "value") else str(slot)


def _is_db_read_source(flow: TaintFlow) -> bool:
    """A read flow: INTERNAL (DB/internal) source reaching a render sink."""
    if _src_value(flow.source_type) != ParameterSource.INTERNAL.value:
        return False
    return _slot_value(flow.sink_slot) not in _NON_RENDER_SLOTS


def _is_db_write(flow: TaintFlow) -> bool:
    """A write flow: user input (non-INTERNAL) reaching a SQL value slot."""
    if _src_value(flow.source_type) == ParameterSource.INTERNAL.value:
        return False
    return _slot_value(flow.sink_slot) in _SQL_WRITE_SLOTS


def _find_stored_xss_synthesis(
    pgraph: ParameterPropagationGraph,
) -> list[dict]:
    """Find read<->write pairs that synthesize a Stored XSS chain.

    Matches a read flow (INTERNAL source -> render sink) with a write flow
    (user input -> SQL value slot) by SHARED FIELD NAME (best-effort).
    Returns list of dicts: {write_source, read_field, render_sink, write_flow,
    read_flow}.

    宁缺勿错拼: if field names cannot be matched, skip (do not force-synthesize).
    """
    if pgraph is None:
        return []
    read_flows = [f for f in pgraph.taint_flows if _is_db_read_source(f)]
    write_flows = [f for f in pgraph.taint_flows if _is_db_write(f)]
    synthesis: list[dict] = []
    for rf in read_flows:
        for wf in write_flows:
            # best-effort field name match (source_param of read == source_param of write)
            if rf.source_param == wf.source_param:
                synthesis.append({
                    "write_source": wf.source_param,
                    "read_field": rf.source_param,
                    "render_sink": rf.sink_call_site_id,
                    "write_flow": wf,
                    "read_flow": rf,
                })
    return synthesis


def _synthesize_stored_candidate(s: dict) -> CandidateChain:
    """Build a CandidateChain for a synthesized Stored XSS flow.

    Spans write (input->DB) + read (DB->render). render_context defaults to
    html_body (common DOM sink) — the precise render context is not recoverable
    from the taint graph; the LLM pass judges effectiveness.
    """
    wf = s["write_flow"]
    rf = s["read_flow"]
    src_type = _src_value(wf.source_type)
    # propagate steps: write steps + a DB hop + read steps
    steps = list(wf.propagation_steps) + list(rf.propagation_steps)
    return CandidateChain(
        vuln_class="xss",
        flow_id=f"stored#{wf.flow_id}#{rf.flow_id}",
        entry_point_id=wf.entry_point_id,
        source_param=wf.source_param,
        source_type=src_type,
        sink_call_site_id=rf.sink_call_site_id,
        sink_slot=_slot_value(rf.sink_slot),
        propagation_steps=steps,
        sanitizer_annotations=[],  # synthesized; annotate_sanitizers re-run in judge
        direction_hint="backward",
        post_sanitize_concat=False,
        render_context="html_body",
    )


def _sink_function_label(sink_call_site_id: str) -> str:
    parts = sink_call_site_id.split(":")
    return parts[2] if len(parts) > 2 else sink_call_site_id


async def build_xss_findings(
    pgraph: ParameterPropagationGraph,
    *,
    llm_client: Callable[..., Awaitable[str]],
    sink_call_sites: dict[str, SinkCallSite] | None = None,
    progress_cb: ProgressCb = None,
) -> list[XssVulnerability]:
    candidates = extract_candidate_chains(
        pgraph, vuln_class="xss", sink_call_sites=sink_call_sites,
    )
    # Append synthesized Stored candidates (DB read<->write cross-table fill).
    for s in _find_stored_xss_synthesis(pgraph):
        candidates.append(_synthesize_stored_candidate(s))

    emitter = ProgressEmitter("chain-verdict", len(candidates), progress_cb)
    findings: list[XssVulnerability] = []
    for i, chain in enumerate(candidates, start=1):
        verdict = await judge_chain_verdict(chain, llm_client=llm_client)
        is_vuln = (verdict.verdict == "vulnerable")
        detail: str | None = None
        if is_vuln:
            detail = (f"XSS-GN-{i:02d} vulnerable: "
                      f"source={chain.source_param} ({chain.entry_point_id}) "
                      f"→ sink={chain.sink_call_site_id}")
        await emitter.tick(detail=detail, hits_delta=1 if is_vuln else 0)
        is_stored = chain.flow_id.startswith("stored#")
        findings.append(XssVulnerability(
            ID=f"XSS-GN-{i:02d}",
            vulnerability_type="Stored" if is_stored else "Reflected",
            externally_exploitable=(verdict.verdict == "vulnerable"),
            confidence=verdict.confidence,
            source=f"{chain.source_param} ({chain.entry_point_id})",
            source_detail=verdict.evidence_chain,
            path=verdict.evidence_chain,
            sink_function=_sink_function_label(chain.sink_call_site_id),
            render_context=_RENDER_CONTEXT.get(chain.render_context, "HTML_BODY"),
            encoding_observed=None,
            verdict=verdict.verdict,
            mismatch_reason=verdict.mismatch_reason,
            witness_payload=verdict.witness_payload,
            source_track="gitnexus",
            evidence_chain=verdict.evidence_chain,
        ))
    await emitter.finalize(
        f"{len(findings)} vulnerable · {len(candidates)} candidates judged")
    logger.info("xss gitnexus-track: %d candidates (incl. %d synthesized Stored) → %d findings",
                len(candidates),
                sum(1 for c in candidates if c.flow_id.startswith("stored#")),
                len(findings))
    return findings
