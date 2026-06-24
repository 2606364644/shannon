"""End-to-end-ish integration: pgraph -> builder -> merger (spec §5.4-5.6 + §4.2).

Validates the closed loop WITHOUT temporal/workflow: pgraph produces GitNexus
findings, which merge (verdict OR) with synthetic LLM findings. Confirms
source_track / evidence_chain survive into the merged exploitation queue.
"""
import pytest

from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph, TaintFlow, PropagationStep,
)
from shannon_core.code_index.models import ParameterSource
from shannon_core.code_index.vuln_chain_builders.injection_builder import (
    build_injection_findings,
)
from shannon_core.code_index.dual_track_merger import merge_dual_track_queues
from shannon_core.models.queue_schemas import InjectionVulnerability


def _flow(slot="sql_value", steps=None):
    return TaintFlow(
        flow_id="app.py:h:1#app.py:h:db.execute:5:0",
        entry_point_id="app.py:h:1", source_param="q",
        source_type=ParameterSource.QUERY_PARAM,
        sink_call_site_id="app.py:h:db.execute:5:0",
        sink_slot=slot, propagation_steps=steps or [],
    )


@pytest.mark.asyncio
async def test_gitnexus_track_merges_with_llm_track_verdict_or():
    """Both tracks flag the same source->sink -> merged 'both', high confidence."""
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow()], language_coverage=["python"],
    )

    async def gn_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db.exec(L5)","mismatch_reason":"concat","confidence":"high"}')

    gn_findings = await build_injection_findings(pgraph, llm_client=gn_llm)
    assert len(gn_findings) == 1
    assert gn_findings[0].source_track == "gitnexus"

    # synthetic LLM-track finding for the SAME source->sink (will dedup+merge)
    llm_findings = [InjectionVulnerability(
        ID="INJ-LLM-01", vulnerability_type="injection",
        externally_exploitable=True, confidence="high",
        source="q (app.py:h:1)", sink_call="app.py:h:db.execute:5:0",
        # merger dedup key includes `path`; both tracks produce it for injection,
        # so the LLM-track finding carries a comparable path to dedup together.
        path="q->db.exec(L5)",
        verdict="safe",  # LLM track said safe
        source_track="llm",
    )]

    merged = merge_dual_track_queues(llm_findings, gn_findings, mode="verdict")
    assert len(merged) == 1
    m = merged[0]
    assert m.merge_source == "both"
    # verdict OR: GN vulnerable + LLM safe -> vulnerable (conservative)
    assert m.verdict == "vulnerable"
    # evidence_chain preserved from GitNexus track
    assert m.evidence_chain is not None


@pytest.mark.asyncio
async def test_gitnexus_only_marked_gitnexus_only():
    """GN track flags a chain LLM track missed -> gitnexus-only, needs_review."""
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow()], language_coverage=["python"],
    )

    async def gn_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db","mismatch_reason":"concat","confidence":"high"}')

    gn_findings = await build_injection_findings(pgraph, llm_client=gn_llm)
    # empty LLM track -> all gitnexus-only
    merged = merge_dual_track_queues([], gn_findings, mode="verdict")
    assert len(merged) == 1
    assert merged[0].merge_source == "gitnexus-only"
    assert merged[0].confidence == "needs_review"


@pytest.mark.asyncio
async def test_empty_pgraph_yields_no_gitnexus_findings():
    """Plan 1 not landed -> GN track empty -> merger is LLM-only."""
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])

    async def gn_llm(prompt, **kw):
        raise AssertionError("no LLM on empty pgraph")

    gn_findings = await build_injection_findings(pgraph, llm_client=gn_llm)
    assert gn_findings == []

    llm_findings = [InjectionVulnerability(
        ID="L1", vulnerability_type="injection", externally_exploitable=True,
        confidence="high", source="q", sink_call="db.exec", verdict="vulnerable",
    )]
    merged = merge_dual_track_queues(llm_findings, gn_findings, mode="verdict")
    assert len(merged) == 1
    assert merged[0].merge_source == "llm-only"
