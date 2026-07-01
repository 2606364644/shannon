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


@pytest.mark.asyncio
async def test_build_injection_findings_reports_chain_progress(monkeypatch):
    """build_injection_findings must drive a chain-verdict ProgressEmitter:
    one tick per candidate + a final summary sample."""
    from shannon_core.code_index.progress import ProgressSample
    from shannon_core.code_index.vuln_chain_builders import injection_builder

    samples: list = []

    async def cb(s: ProgressSample):
        samples.append(s)

    # 3 candidate chains (sql_value slot picked up by injection builder)
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value") for _ in range(3)],
        language_coverage=["python"],
    )

    # Mark every chain vulnerable so each tick should carry INJ-GN-NN detail.
    async def fake_judge(chain, *, llm_client):
        return type("V", (), {
            "verdict": "vulnerable", "confidence": "high",
            "evidence_chain": "q->db", "mismatch_reason": "concat",
            "witness_payload": "'",
        })()
    monkeypatch.setattr(injection_builder, "judge_chain_verdict", fake_judge)

    async def fake_llm(prompt, **kw):
        raise AssertionError("judge_chain_verdict is mocked; llm unused")

    findings = await build_injection_findings(pgraph, llm_client=fake_llm, progress_cb=cb)

    # 3 candidates → 3 non-final ticks, then 1 final
    non_final = [s for s in samples if not s.final]
    finals = [s for s in samples if s.final]
    assert len(non_final) == 3
    assert len(finals) == 1
    # phase + counters
    assert all(s.phase == "chain-verdict" for s in samples)
    assert non_final[-1].done == 3 and non_final[-1].total == 3
    assert non_final[-1].hits == 3            # all vulnerable
    # vulnerable tick detail carries INJ-GN-NN prefix + source→sink
    assert non_final[0].detail.startswith("INJ-GN-01 vulnerable:")
    assert "→ sink=" in non_final[0].detail
    # final summary mentions the count
    assert "3 vulnerable" in finals[0].detail
    # findings still produced unchanged
    assert len(findings) == 3


@pytest.mark.asyncio
async def test_build_injection_findings_progress_cb_none_is_noop():
    """progress_cb=None must keep the builder working exactly as before."""
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value")], language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db","mismatch_reason":"concat","confidence":"high"}')

    findings = await build_injection_findings(pgraph, llm_client=fake_llm, progress_cb=None)
    assert len(findings) == 1
