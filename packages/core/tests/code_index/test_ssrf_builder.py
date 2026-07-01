import pytest

from shannon_core.code_index.vuln_chain_builders.ssrf_builder import (
    build_ssrf_findings,
)
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph, TaintFlow,
)
from shannon_core.code_index.models import ParameterSource
from shannon_core.models.queue_schemas import SsrfVulnerability


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
    """build_ssrf_findings must drive a chain-verdict ProgressEmitter."""
    from shannon_core.code_index.progress import ProgressSample
    from shannon_core.code_index.vuln_chain_builders import ssrf_builder

    samples: list = []

    async def cb(s: ProgressSample):
        samples.append(s)

    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow(), _flow(source="callback")],
        language_coverage=["python"],
    )

    async def fake_judge(chain, *, llm_client):
        return type("V", (), {
            "verdict": "vulnerable", "confidence": "high",
            "evidence_chain": "url->fetch", "mismatch_reason": "no allowlist",
            "witness_payload": "http://127.0.0.1/",
        })()
    monkeypatch.setattr(ssrf_builder, "judge_chain_verdict", fake_judge)

    async def fake_llm(prompt, **kw):
        raise AssertionError("judge_chain_verdict is mocked; llm unused")

    findings = await build_ssrf_findings(pgraph, llm_client=fake_llm, progress_cb=cb)

    non_final = [s for s in samples if not s.final]
    finals = [s for s in samples if s.final]
    assert len(non_final) == 2
    assert len(finals) == 1
    assert all(s.phase == "chain-verdict" for s in samples)
    assert non_final[-1].done == 2 and non_final[-1].total == 2
    assert non_final[-1].hits == 2
    # vulnerable tick detail carries SSRF-GN-NN prefix
    assert non_final[0].detail.startswith("SSRF-GN-01 vulnerable:")
    assert "→ sink=" in non_final[0].detail
    assert "2 vulnerable" in finals[0].detail
    assert len(findings) == 2
