import pytest

from shannon_core.code_index.vuln_chain_builders.xss_builder import (
    build_xss_findings, _find_stored_xss_synthesis,
)
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph, TaintFlow, PropagationStep, SinkCallSite, SinkCategory,
)
from shannon_core.code_index.models import ParameterSource
from shannon_core.models.queue_schemas import XssVulnerability


def _flow(slot="generic", source="name", source_type=ParameterSource.QUERY_PARAM,
          sink_id="app.py:h:innerHTML:5:0", steps=None):
    return TaintFlow(
        flow_id=f"ep#{sink_id}", entry_point_id="app.py:h:1",
        source_param=source, source_type=source_type,
        sink_call_site_id=sink_id, sink_slot=slot,
        propagation_steps=steps or [],
    )


def _step(tf):
    return PropagationStep(step_id="s", from_func_id="f", from_param="q",
                           to_func_id="f", to_param="x", transformation=tf,
                           code_location="app.py:3")


def _xss_sink(sink_id, sink_subtype="xss_innerhtml"):
    return SinkCallSite(
        id=sink_id, caller_id="app.py:h", callee_name="innerHTML",
        callee_receiver="el", category=SinkCategory.XSS, sink_subtype=sink_subtype,
        file_path="app.py", line=5, column=10, dangerous_slots=[], rule_id="xss-rule",
    )


@pytest.mark.asyncio
async def test_build_xss_reflected_vulnerable():
    sid = "app.py:h:innerHTML:5:0"
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("generic", source="q", sink_id=sid)],
        language_coverage=["typescript"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"><script>","evidence_chain":'
                '"q->innerHTML","mismatch_reason":"no encoding for html body","confidence":"high"}')

    findings = await build_xss_findings(
        pgraph, llm_client=fake_llm, sink_call_sites={sid: _xss_sink(sid)})
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, XssVulnerability)
    assert f.vulnerability_type in ("Reflected", "Stored", "DOM-based")
    assert f.render_context == "HTML_BODY"
    assert f.verdict == "vulnerable"
    assert f.source_track == "gitnexus"


def test_find_stored_xss_synthesis_matches_read_to_write_by_field():
    """read flow (DB->render, INTERNAL source 'name') + write flow (input->DB 'name') -> synthesis."""
    read_flow = _flow("generic", source="name", source_type=ParameterSource.INTERNAL,
                      sink_id="app.py:profile:innerHTML:10:0")
    write_flow = TaintFlow(
        flow_id="ep2#app.py:save:db.execute:5:0", entry_point_id="app.py:save:1",
        source_param="name", source_type=ParameterSource.BODY_FIELD,
        sink_call_site_id="app.py:save:db.execute:5:0",
        sink_slot="sql_value",  # SQL write
        propagation_steps=[],
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[read_flow, write_flow], language_coverage=["python"],
    )
    synthesis = _find_stored_xss_synthesis(pgraph)
    assert len(synthesis) == 1
    s = synthesis[0]
    assert s["write_source"] == "name"
    assert s["read_field"] == "name"
    assert "innerHTML" in s["render_sink"]


def test_find_stored_xss_synthesis_skips_when_no_matching_write():
    read_flow = _flow("generic", source="name", source_type=ParameterSource.INTERNAL)
    # no write flow with same field
    write_flow = TaintFlow(
        flow_id="ep2#w", entry_point_id="app.py:s:1", source_param="other",
        source_type=ParameterSource.BODY_FIELD,
        sink_call_site_id="app.py:s:db.execute:5:0", sink_slot="sql_value",
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[read_flow, write_flow], language_coverage=["python"],
    )
    assert _find_stored_xss_synthesis(pgraph) == []


@pytest.mark.asyncio
async def test_build_xss_synthesizes_stored_finding():
    """read flow (INTERNAL source) + matching write flow -> extra Stored finding."""
    read_flow = _flow("generic", source="bio", source_type=ParameterSource.INTERNAL,
                      sink_id="app.py:profile:innerHTML:10:0")
    write_flow = TaintFlow(
        flow_id="ep2#app.py:save:db.execute:5:0", entry_point_id="app.py:save:1",
        source_param="bio", source_type=ParameterSource.BODY_FIELD,
        sink_call_site_id="app.py:save:db.execute:5:0", sink_slot="sql_value",
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[read_flow, write_flow], language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"><img>","evidence_chain":'
                '"bio(input)->DB->read->innerHTML","mismatch_reason":"stored, no encode",'
                '"confidence":"high"}')

    findings = await build_xss_findings(pgraph, llm_client=fake_llm)
    stored = [f for f in findings if f.vulnerability_type == "Stored"]
    assert len(stored) >= 1


@pytest.mark.asyncio
async def test_build_xss_empty_pgraph_returns_empty():
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])

    async def fake_llm(prompt, **kw):
        raise AssertionError("no LLM on empty pgraph")

    assert await build_xss_findings(pgraph, llm_client=fake_llm) == []
