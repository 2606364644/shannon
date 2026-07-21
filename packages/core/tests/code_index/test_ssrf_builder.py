import pytest

from supernova_core.code_index.chain_verdict import ChainVerdict
from supernova_core.code_index.vuln_chain_builders.ssrf_builder import (
    build_ssrf_findings,
)
from supernova_core.code_index.parameter_models import (
    DangerousSlot, SlotContext, SinkCallSite, SinkCategory,
    ParameterPropagationGraph, TaintFlow,
)
from supernova_core.code_index.models import ParameterSource
from supernova_core.models.queue_schemas import SsrfVulnerability


def _flow(source="url", source_type=ParameterSource.QUERY_PARAM):
    return TaintFlow(
        flow_id="ep#app.py:fetch:5:0", entry_point_id="app.py:proxy:1",
        source_param=source, source_type=source_type,
        sink_call_site_id="app.py:proxy:fetch:5:0", sink_slot="url",
        propagation_steps=[],
    )


@pytest.mark.asyncio
async def test_build_ssrf_vulnerable():
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow()], language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"http://127.0.0.1:22/",'
                '"evidence_chain":"url->fetch(L5)","mismatch_reason":"no allowlist",'
                '"confidence":"high"}')

    findings = await build_ssrf_findings(pgraph, llm_client=fake_llm)
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, SsrfVulnerability)
    assert f.verdict == "vulnerable"  # uses new Task 2 field
    assert f.path == "url->fetch(L5)"
    assert f.witness_payload == "http://127.0.0.1:22/"
    assert f.missing_defense == "no allowlist"
    assert f.vulnerable_code_location == "app.py:proxy:fetch:5:0"
    assert f.source_track == "gitnexus"


@pytest.mark.asyncio
async def test_build_ssrf_skips_non_url_slots():
    pgraph = ParameterPropagationGraph(
        taint_flows=[TaintFlow(
            flow_id="x", entry_point_id="e", source_param="q",
            source_type=ParameterSource.QUERY_PARAM,
            sink_call_site_id="db.exec:1", sink_slot="sql_value",
        )], language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        raise AssertionError("url slot only")

    assert await build_ssrf_findings(pgraph, llm_client=fake_llm) == []


@pytest.mark.asyncio
async def test_build_ssrf_empty_pgraph_returns_empty():
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])

    async def fake_llm(prompt, **kw):
        raise AssertionError("no LLM on empty")

    assert await build_ssrf_findings(pgraph, llm_client=fake_llm) == []


@pytest.mark.asyncio
async def test_build_ssrf_findings_reports_chain_progress(monkeypatch):
    """progress_cb receives a tick per candidate + a final summary sample."""
    samples: list = []

    async def cb(s):
        samples.append(s)

    # 2 url candidates
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow(source="url1"), _flow(source="url2")],
        language_coverage=["python"],
    )

    call_count = {"n": 0}

    async def fake_judge(chain, *, llm_client):
        call_count["n"] += 1
        is_vuln = (call_count["n"] == 1)  # first chain vulnerable
        return ChainVerdict(
            verdict="vulnerable" if is_vuln else "safe",
            witness_payload="http://127.0.0.1/" if is_vuln else None,
            evidence_chain="url->fetch",
            mismatch_reason=None,
            confidence="high",
        )

    monkeypatch.setattr(
        "supernova_core.code_index.vuln_chain_builders.ssrf_builder.judge_chain_verdict",
        fake_judge,
    )

    async def fake_llm(prompt, **kw):
        raise AssertionError("judge is monkeypatched; llm_client unused")

    findings = await build_ssrf_findings(
        pgraph, llm_client=fake_llm, progress_cb=cb)

    assert len(findings) == 2
    non_final = [s for s in samples if not s.final]
    assert len(non_final) == 2
    assert samples[-1].final is True
    assert samples[-1].total == 2
    hit_samples = [s for s in non_final if s.detail]
    assert len(hit_samples) == 1
    assert hit_samples[0].detail.startswith("SSRF-GN-01")
    assert hit_samples[0].hits == 1


@pytest.mark.asyncio
async def test_build_ssrf_findings_progress_cb_none_no_raise():
    """cb=None (default) must run without raising."""
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow()], language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"safe","witness_payload":null,"evidence_chain":'
                '"url->fetch","mismatch_reason":null,"confidence":"high"}')

    findings = await build_ssrf_findings(pgraph, llm_client=fake_llm)
    assert len(findings) == 1


@pytest.mark.asyncio
async def test_build_ssrf_accepts_sink_call_sites_param():
    """ssrf builder accepts sink_call_sites (forwarding contract).

    sink_expressions reaching the verdict PROMPT is covered by Task 6 (prompt
    wiring) and the Task 7 end-to-end test. Here we only lock the builder
    signature + that forwarding does not break extract→judge.
    """
    sid = "app.py:proxy:fetch:5:0"
    sink = SinkCallSite(
        id=sid, caller_id="app.py:proxy", callee_name="fetch",
        callee_receiver="http", category=SinkCategory.SSRF,
        sink_subtype="ssrf_http_client", file_path="app.py", line=5, column=10,
        dangerous_slots=[DangerousSlot(
            arg_index=0, slot=SlotContext.URL,
            expression="'http://api/' + user_url", is_entry_hint=True)],
        rule_id="py-ssrf-fetch",
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow()], language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"safe","witness_payload":null,"evidence_chain":'
                '"url->fetch","mismatch_reason":null,"confidence":"high"}')

    findings = await build_ssrf_findings(
        pgraph, llm_client=fake_llm, sink_call_sites={sid: sink})
    assert isinstance(findings, list)
    assert len(findings) == 1
