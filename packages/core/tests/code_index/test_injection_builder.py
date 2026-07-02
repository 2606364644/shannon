import pytest

from shannon_core.code_index.vuln_chain_builders.injection_builder import (
    build_injection_findings,
)
from shannon_core.code_index.chain_verdict import ChainVerdict
from shannon_core.code_index.parameter_models import (
    DangerousSlot, SlotContext, SinkCallSite, SinkCategory,
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


@pytest.mark.asyncio
async def test_build_injection_findings_reports_chain_progress(monkeypatch):
    """progress_cb receives a tick per candidate + a final summary sample."""
    samples: list = []

    async def cb(s):
        samples.append(s)

    # 3 injection candidate chains
    pgraph = ParameterPropagationGraph(
        taint_flows=[
            _flow("sql_value", steps=[_step("concat")]),
            _flow("sql_value", steps=[_step("concat")]),
            _flow("sql_value", steps=[_step("concat")]),
        ],
        language_coverage=["python"],
    )

    call_count = {"n": 0}

    async def fake_judge(chain, *, llm_client):
        call_count["n"] += 1
        # first chain vulnerable, others safe
        is_vuln = (call_count["n"] == 1)
        return ChainVerdict(
            verdict="vulnerable" if is_vuln else "safe",
            witness_payload="'" if is_vuln else None,
            evidence_chain="q->db",
            mismatch_reason=None,
            confidence="high",
        )

    monkeypatch.setattr(
        "shannon_core.code_index.vuln_chain_builders.injection_builder.judge_chain_verdict",
        fake_judge,
    )

    async def fake_llm(prompt, **kw):
        raise AssertionError("judge is monkeypatched; llm_client unused")

    findings = await build_injection_findings(
        pgraph, llm_client=fake_llm, progress_cb=cb)

    assert len(findings) == 3
    # 3 candidates -> 3 non-final ticks
    non_final = [s for s in samples if not s.final]
    assert len(non_final) == 3
    assert samples[-1].final is True
    assert samples[-1].total == 3
    # one hit (first chain vulnerable) -> hit sample with INJ-GN- detail prefix
    hit_samples = [s for s in non_final if s.detail]
    assert len(hit_samples) == 1
    assert hit_samples[0].detail.startswith("INJ-GN-01")
    assert hit_samples[0].hits == 1
    # safe chains have empty detail
    safe_samples = [s for s in non_final if not s.detail]
    assert len(safe_samples) == 2


@pytest.mark.asyncio
async def test_build_injection_findings_progress_cb_none_no_raise():
    """cb=None (default) must run without raising."""
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", steps=[_step("concat")])],
        language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db","mismatch_reason":null,"confidence":"high"}')

    findings = await build_injection_findings(pgraph, llm_client=fake_llm)
    assert len(findings) == 1


@pytest.mark.asyncio
async def test_build_injection_accepts_sink_call_sites_param():
    """inj builder accepts sink_call_sites (forwarding contract).

    sink_expressions reaching the verdict PROMPT is covered by Task 6 (prompt
    wiring) and the Task 7 end-to-end test (asserts the expression is in the
    prompt). Here we only lock the builder signature + that forwarding does
    not break extract→judge.
    """
    sid = "app.py:handler:db.execute:5:0"
    sink = SinkCallSite(
        id=sid, caller_id="app.py:handler", callee_name="execute",
        callee_receiver="db", category=SinkCategory.SQL,
        sink_subtype="sql_raw_query", file_path="app.py", line=5, column=10,
        dangerous_slots=[DangerousSlot(
            arg_index=0, slot=SlotContext.SQL_VALUE,
            expression="'SELECT * FROM t WHERE id=' + q", is_entry_hint=True)],
        rule_id="py-sql-execute",
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value")], language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db","mismatch_reason":"x","confidence":"high"}')

    findings = await build_injection_findings(
        pgraph, llm_client=fake_llm, sink_call_sites={sid: sink})
    assert isinstance(findings, list)
    assert len(findings) == 1
