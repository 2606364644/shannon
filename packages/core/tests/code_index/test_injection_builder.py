import pytest

from shannon_core.code_index.vuln_chain_builders.injection_builder import (
    build_injection_findings,
)
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph, TaintFlow, PropagationStep,
)
from shannon_core.code_index.models import ParameterSource
from shannon_core.models.queue_schemas import InjectionVulnerability


def _flow(slot, steps=None):
    return TaintFlow(
        flow_id="app.py:handler:1#app.py:handler:db.execute:5:0",
        entry_point_id="app.py:handler:1", source_param="q",
        source_type=ParameterSource.QUERY_PARAM,
        sink_call_site_id="app.py:handler:db.execute:5:0",
        sink_slot=slot, propagation_steps=steps or [],
    )


def _step(tf):
    return PropagationStep(step_id="s", from_func_id="f", from_param="q",
                           to_func_id="f", to_param="x", transformation=tf,
                           code_location="app.py:3")


@pytest.mark.asyncio
async def test_build_injection_findings_vulnerable_chain():
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", steps=[_step("concat")])],
        language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db.exec","mismatch_reason":"concat into value slot","confidence":"high"}')

    findings = await build_injection_findings(pgraph, llm_client=fake_llm)
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, InjectionVulnerability)
    assert f.vulnerability_type == "injection"
    assert f.verdict == "vulnerable"
    assert f.slot_type == "SQL-val"  # sql_value -> SQL-val mapping
    assert f.sink_call == "app.py:handler:db.execute:5:0"
    assert f.witness_payload == "'"
    # GitNexus-track evidence chain
    assert f.evidence_chain == "q->db.exec"
    assert f.source_track == "gitnexus"


@pytest.mark.asyncio
async def test_build_injection_flags_post_sanitize_concat():
    steps = [_step("sanitize_hint:html.escape"), _step("concat")]
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", steps=steps)], language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db","mismatch_reason":"post-sanitize concat","confidence":"high"}')

    findings = await build_injection_findings(pgraph, llm_client=fake_llm)
    assert len(findings) == 1
    assert "post-sanitize concat" in (findings[0].concat_occurrences or "").lower()


@pytest.mark.asyncio
async def test_build_injection_empty_pgraph_returns_empty():
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])

    async def fake_llm(prompt, **kw):
        raise AssertionError("should not call LLM on empty pgraph")

    findings = await build_injection_findings(pgraph, llm_client=fake_llm)
    assert findings == []


@pytest.mark.asyncio
async def test_build_injection_skips_non_injection_slots():
    # ssrf url slot should NOT be picked up by injection builder
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("url")], language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        raise AssertionError("should not call LLM on url slot")

    findings = await build_injection_findings(pgraph, llm_client=fake_llm)
    assert findings == []
