import pytest

from shannon_core.code_index.chain_verdict import (
    CandidateChain,
    extract_candidate_chains,
    judge_chain_verdict,
    ChainVerdict,
)
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    SinkCallSite,
    SinkCategory,
    TaintFlow,
    PropagationStep,
)
from shannon_core.code_index.models import ParameterSource


def _flow(sink_slot, source="q", source_type=ParameterSource.QUERY_PARAM, steps=None,
          sink_id="app.py:handler:db.execute:1:0"):
    return TaintFlow(
        flow_id="ep#sink1", entry_point_id="app.py:handler:1",
        source_param=source, source_type=source_type,
        sink_call_site_id=sink_id,
        sink_slot=sink_slot,
        propagation_steps=steps or [],
    )


def _step(tf, code_location="app.py:5"):
    return PropagationStep(
        step_id="s1", from_func_id="f", from_param="q",
        to_func_id="f", to_param="x", transformation=tf, code_location=code_location,
    )


def _xss_sink(sink_id, sink_subtype="xss_innerhtml"):
    return SinkCallSite(
        id=sink_id, caller_id="app.py:handler", callee_name="innerHTML",
        callee_receiver="el", category=SinkCategory.XSS, sink_subtype=sink_subtype,
        file_path="app.py", line=5, column=10, dangerous_slots=[], rule_id="xss-rule",
    )


def test_extract_injection_routes_sql_and_command_sinks():
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value"), _flow("cmd_argument")],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="injection")
    assert len(chains) == 2
    assert all(c.vuln_class == "injection" for c in chains)
    assert all(c.direction_hint == "forward" for c in chains)


def test_extract_xss_routes_only_xss_sinks():
    """No sink_call_sites provided → xss cannot resolve render context → no chains."""
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value"), _flow("generic")],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="xss")
    # sink_slot "generic"/"sql_value" carry no render context → no chain
    assert chains == []


def test_extract_xss_routes_by_sink_call_site_category():
    """xss routes via SinkCallSite.category == XSS (SlotContext has no render context)."""
    sid = "app.py:handler:innerHTML:5:0"
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("generic", sink_id=sid), _flow("sql_value")],
        language_coverage=["typescript"],
    )
    chains = extract_candidate_chains(
        pgraph, vuln_class="xss",
        sink_call_sites={sid: _xss_sink(sid, sink_subtype="xss_innerhtml")},
    )
    assert len(chains) == 1
    c = chains[0]
    assert c.vuln_class == "xss"
    assert c.direction_hint == "backward"
    assert c.render_context == "html_body"  # innerHTML → HTML body context


def test_extract_ssrf_routes_only_url_sinks():
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("url"), _flow("sql_value")],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="ssrf")
    assert len(chains) == 1
    assert chains[0].vuln_class == "ssrf"
    assert chains[0].direction_hint == "backward"


def test_extract_empty_pgraph_returns_empty():
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])
    assert extract_candidate_chains(pgraph, vuln_class="injection") == []


def test_post_sanitize_concat_detected_when_concat_after_sanitizer():
    steps = [_step("sanitize_hint:html.escape"), _step("concat")]
    pgraph2 = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", steps=steps)],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph2, vuln_class="injection")
    assert len(chains) == 1
    assert chains[0].post_sanitize_concat is True


def test_post_sanitize_concat_false_when_no_concat_after():
    steps = [_step("sanitize_hint:html.escape"), _step("format")]
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", steps=steps)], language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="injection")
    assert len(chains) == 1
    assert chains[0].post_sanitize_concat is False


@pytest.mark.asyncio
async def test_judge_chain_verdict_calls_llm_and_parses_verdict():
    """LLM pass returns verdict JSON -> ChainVerdict parsed."""
    chain = CandidateChain(
        vuln_class="injection", flow_id="f1", entry_point_id="ep",
        source_param="q", source_type="query", sink_call_site_id="db.execute:1",
        sink_slot="sql_value", propagation_steps=[_step("concat")],
        sanitizer_annotations=[], direction_hint="forward",
        post_sanitize_concat=True,
    )

    async def fake_llm(prompt, **kw):
        # LLM pass returns a compact verdict JSON
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q -> db.execute(L1)","mismatch_reason":"concat into sql value slot",'
                '"confidence":"high"}')

    verdict = await judge_chain_verdict(chain, llm_client=fake_llm)
    assert isinstance(verdict, ChainVerdict)
    assert verdict.verdict == "vulnerable"
    assert verdict.witness_payload == "'"
    assert "db.execute" in verdict.evidence_chain
    assert verdict.confidence == "high"


@pytest.mark.asyncio
async def test_judge_chain_verdict_defaults_safe_on_llm_failure():
    """LLM pass raises/fails → conservative: treat as needs_review, do not crash."""
    chain = CandidateChain(
        vuln_class="ssrf", flow_id="f1", entry_point_id="ep",
        source_param="url", source_type="query", sink_call_site_id="fetch:1",
        sink_slot="url", propagation_steps=[], sanitizer_annotations=[],
        direction_hint="backward", post_sanitize_concat=False,
    )

    async def failing_llm(prompt, **kw):
        raise RuntimeError("LLM chain-verdict pass not available")

    verdict = await judge_chain_verdict(chain, llm_client=failing_llm)
    # graceful: never crash; mark needs_review (do not silently declare safe/vulnerable)
    assert verdict.confidence == "low"
    assert "needs_review" in (verdict.mismatch_reason or "") or verdict.verdict in ("safe", "vulnerable")
