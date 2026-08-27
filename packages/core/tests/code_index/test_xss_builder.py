import pytest

from supernova_core.code_index.chain_verdict import ChainVerdict
from supernova_core.code_index.vuln_chain_builders.xss_builder import (
    build_xss_findings, _find_stored_xss_synthesis,
)
from supernova_core.code_index.parameter_models import (
    ParameterPropagationGraph, TaintFlow, PropagationStep, SinkCallSite, SinkCategory,
)
from supernova_core.code_index.models import EntryPoint, ParameterSource
from supernova_core.models.queue_schemas import XssVulnerability


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


@pytest.mark.asyncio
async def test_build_xss_findings_reports_chain_progress(monkeypatch):
    """progress_cb receives a tick per candidate + a final summary sample."""
    samples: list = []

    async def cb(s):
        samples.append(s)

    sid = "app.py:h:innerHTML:5:0"
    # 2 reflected candidates
    pgraph = ParameterPropagationGraph(
        taint_flows=[
            _flow("generic", source="q1", sink_id=sid),
            _flow("generic", source="q2", sink_id=sid),
        ],
        language_coverage=["typescript"],
    )

    call_count = {"n": 0}

    async def fake_judge(chain, *, llm_client=None, verdict_agent=None,
                         agent_name=None):
        call_count["n"] += 1
        is_vuln = (call_count["n"] == 2)  # second chain vulnerable
        return ChainVerdict(
            verdict="vulnerable" if is_vuln else "safe",
            witness_payload="><script>" if is_vuln else None,
            evidence_chain="q->innerHTML",
            mismatch_reason=None,
            confidence="high",
        )

    monkeypatch.setattr(
        "supernova_core.code_index.vuln_chain_builders.xss_builder.judge_chain_verdict",
        fake_judge,
    )

    async def fake_llm(prompt, **kw):
        raise AssertionError("judge is monkeypatched; llm_client unused")

    findings = await build_xss_findings(
        pgraph, llm_client=fake_llm,
        sink_call_sites={sid: _xss_sink(sid)},
        progress_cb=cb,
    )

    assert len(findings) == 2
    non_final = [s for s in samples if not s.final]
    assert len(non_final) == 2
    assert samples[-1].final is True
    assert samples[-1].total == 2
    hit_samples = [s for s in non_final if s.detail]
    assert len(hit_samples) == 1
    assert hit_samples[0].detail.startswith("XSS-GN-02")
    assert hit_samples[0].hits == 1


@pytest.mark.asyncio
async def test_build_xss_findings_progress_cb_none_no_raise():
    """cb=None (default) must run without raising."""
    sid = "app.py:h:innerHTML:5:0"
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("generic", source="q", sink_id=sid)],
        language_coverage=["typescript"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"safe","witness_payload":null,"evidence_chain":'
                '"q->innerHTML","mismatch_reason":null,"confidence":"high"}')

    findings = await build_xss_findings(
        pgraph, llm_client=fake_llm, sink_call_sites={sid: _xss_sink(sid)})
    assert len(findings) == 1


@pytest.mark.asyncio
async def test_build_xss_finding_carries_title():
    """chain verdict 的 title 透传到 XssVulnerability.title。"""
    sid = "app.py:h:innerHTML:5:0"
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("generic", source="q", sink_id=sid)],
        language_coverage=["typescript"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"><script>","evidence_chain":'
                '"q->innerHTML","mismatch_reason":"no encoding","confidence":"high",'
                '"title":"Reflected XSS via q param into innerHTML"}')

    findings = await build_xss_findings(
        pgraph, llm_client=fake_llm, sink_call_sites={sid: _xss_sink(sid)})
    assert findings[0].title == "Reflected XSS via q param into innerHTML"


@pytest.mark.asyncio
async def test_build_xss_finding_carries_sink_call():
    """F1 折叠修复：XssVulnerability 回填 sink_call（sink_call_site_id 全标识）——
    gn_collapse._unit_key 靠它折叠 GN 单元（NodeGoat 15 条笛卡尔积链一条没折的
    根因），affected_entries[].sink_location 也由它解析（对齐 injection_builder
    sink_call 先例，test_injection_builder.py:57）。"""
    sid = "app.py:h:innerHTML:5:0"
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("generic", source="q", sink_id=sid)],
        language_coverage=["typescript"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"><script>",'
                '"evidence_chain":"q->innerHTML","mismatch_reason":"x",'
                '"confidence":"high"}')

    findings = await build_xss_findings(
        pgraph, llm_client=fake_llm, sink_call_sites={sid: _xss_sink(sid)})
    assert findings[0].sink_call == sid


@pytest.mark.asyncio
async def test_build_xss_entry_points_prefixes_path_with_route():
    """O2 前半：entry_points join 命中 → path 带 "METHOD /path" 前缀；miss → 原样。"""
    sid = "app.py:h:innerHTML:5:0"
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("generic", source="q", sink_id=sid)],
        language_coverage=["typescript"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"><script>",'
                '"evidence_chain":"q->innerHTML","mismatch_reason":"x",'
                '"confidence":"high"}')

    ep = EntryPoint(
        func_block_id="app.py:h:1", entry_type="http_route",
        route="/comment", http_method="POST", confidence=1.0,
        evidence="annot", needs_llm_review=False,
    )
    findings = await build_xss_findings(
        pgraph, llm_client=fake_llm, sink_call_sites={sid: _xss_sink(sid)},
        entry_points={"app.py:h:1": ep})
    assert findings[0].path == "POST /comment → q->innerHTML"

    findings = await build_xss_findings(
        pgraph, llm_client=fake_llm, sink_call_sites={sid: _xss_sink(sid)})
    assert findings[0].path == "q->innerHTML"
