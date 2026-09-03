import pytest
from types import SimpleNamespace

from supernova_core.code_index.chain_verdict import ChainVerdict
from supernova_core.code_index.vuln_chain_builders.ssrf_builder import (
    build_ssrf_findings,
)
from supernova_core.code_index.parameter_models import (
    DangerousSlot, SlotContext, SinkCallSite, SinkCategory,
    ParameterPropagationGraph, TaintFlow,
)
from supernova_core.code_index.models import EntryPoint, ParameterSource
from supernova_core.models.queue_schemas import SsrfVulnerability

def _agent(payload: str = ""):
    """fake verdict_agent（SimpleNamespace 模拟 ClaudeRunResult，text 兜底解析）。"""
    async def agent(prompt, *, output_format=None, agent_name=None):
        return SimpleNamespace(success=True, structured_output=None,
                               text=payload, error=None)
    return agent


def _never_agent():
    """不应被调用的守卫 agent（调用即断言失败）。"""
    async def agent(prompt, *, output_format=None, agent_name=None):
        raise AssertionError("verdict agent must not be called")
    return agent



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

    findings = await build_ssrf_findings(pgraph, verdict_agent=_agent(
            '{"verdict":"vulnerable","witness_payload":"http://127.0.0.1:22/",'
            '"evidence_chain":"url->fetch(L5)","mismatch_reason":"no allowlist",'
            '"confidence":"high"}'))
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

    assert await build_ssrf_findings(pgraph, verdict_agent=_never_agent()) == []


@pytest.mark.asyncio
async def test_build_ssrf_empty_pgraph_returns_empty():
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])

    assert await build_ssrf_findings(pgraph, verdict_agent=_never_agent()) == []


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

    async def fake_judge(chain, *, verdict_agent=None, agent_name=None):
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
        "supernova_core.code_index.chain_verdict.judge_chain_verdict",
        fake_judge,
    )

    findings = await build_ssrf_findings(
        pgraph, verdict_agent=_never_agent(), progress_cb=cb)

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

    findings = await build_ssrf_findings(pgraph, verdict_agent=_agent(
            '{"verdict":"safe","witness_payload":null,"evidence_chain":'
            '"url->fetch","mismatch_reason":null,"confidence":"high"}'))
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

    findings = await build_ssrf_findings(
        pgraph, verdict_agent=_agent(
            '{"verdict":"safe","witness_payload":null,"evidence_chain":'
            '"url->fetch","mismatch_reason":null,"confidence":"high"}'), sink_call_sites={sid: sink})
    assert isinstance(findings, list)
    assert len(findings) == 1


@pytest.mark.asyncio
async def test_build_ssrf_open_redirect_sink_reports_open_redirect_subtype():
    """spec 2026-08-21 修复点 E: REDIRECT sink 不再过滤,改产 Open_Redirect 子型。

    原行为(过滤丢弃)的开轨假设——LLM 轨会以 Open_Redirect 报——在关轨时破产:
    两轨同时静默(NodeGoat /learn res.redirect(req.query.url) 漏报,断点 4)。
    merger dedup key 含 vulnerability_type(_finding_key),GitNexus 产
    Open_Redirect 与 LLM 轨枚举(vuln-ssrf.txt:107)对齐,开轨不重复。
    """
    sid = "app.py:proxy:fetch:5:0"
    redirect_sink = SinkCallSite(
        id=sid, caller_id="app.py:proxy", callee_name="redirect",
        callee_receiver="res", category=SinkCategory.REDIRECT,
        sink_subtype="open_redirect", file_path="app.py", line=72, column=10,
        dangerous_slots=[DangerousSlot(
            arg_index=0, slot=SlotContext.URL, expression="req.query.url",
            is_entry_hint=True)],
        rule_id="ts-res-redirect",
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow()], language_coverage=["javascript"],
    )

    findings = await build_ssrf_findings(
        pgraph, verdict_agent=_agent(
            '{"verdict":"vulnerable","witness_payload":"https://evil.com/",'
            '"evidence_chain":"url->redirect","mismatch_reason":"no allowlist",'
            '"confidence":"high"}'), sink_call_sites={sid: redirect_sink})
    assert len(findings) == 1, "REDIRECT 候选须产出(不再过滤)"
    f = findings[0]
    assert f.vulnerability_type == "Open_Redirect", \
        "REDIRECT sink 产 Open_Redirect 子型(对齐 LLM 轨枚举 + merger dedup key)"
    assert f.verdict == "vulnerable"


@pytest.mark.asyncio
async def test_build_ssrf_non_redirect_keeps_url_manipulation():
    """非 REDIRECT(真 SSRF fetch)保持 vulnerability_type=URL_Manipulation,不混淆。"""
    sid = "app.py:proxy:fetch:5:0"
    fetch_sink = SinkCallSite(
        id=sid, caller_id="app.py:proxy", callee_name="get",
        callee_receiver="needle", category=SinkCategory.SSRF,
        sink_subtype="ssrf_needle", file_path="app.py", line=16, column=10,
        dangerous_slots=[DangerousSlot(
            arg_index=0, slot=SlotContext.URL, expression="url",
            is_entry_hint=False)],
        rule_id="ts-needle-get",
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow()], language_coverage=["javascript"],
    )

    findings = await build_ssrf_findings(
        pgraph, verdict_agent=_agent(
            '{"verdict":"vulnerable","witness_payload":"http://169.254.169.254/",'
            '"evidence_chain":"url->needle.get","mismatch_reason":"no allowlist",'
            '"confidence":"high"}'), sink_call_sites={sid: fetch_sink})
    assert len(findings) == 1
    assert findings[0].vulnerability_type == "URL_Manipulation"


@pytest.mark.asyncio
async def test_build_ssrf_finding_carries_title():
    """chain verdict 的 title 透传到 SsrfVulnerability.title。"""
    from supernova_core.code_index.parameter_models import (
        ParameterPropagationGraph, TaintFlow,
    )
    from supernova_core.code_index.models import ParameterSource
    pgraph = ParameterPropagationGraph(
        taint_flows=[TaintFlow(
            flow_id="app.py:proxy:1#app.py:proxy:fetch:5:0",
            entry_point_id="app.py:proxy:1", source_param="url",
            source_type=ParameterSource.QUERY_PARAM,
            sink_call_site_id="app.py:proxy:fetch:5:0",
            sink_slot="url", propagation_steps=[],
        )],
        language_coverage=["python"],
    )

    findings = await build_ssrf_findings(pgraph, verdict_agent=_agent(
            '{"verdict":"vulnerable","witness_payload":"http://127.0.0.1/",'
            '"evidence_chain":"url->fetch(L5)","mismatch_reason":"no allowlist",'
            '"confidence":"high","title":"SSRF via url param in /proxy"}'))
    assert findings[0].title == "SSRF via url param in /proxy"


@pytest.mark.asyncio
async def test_build_ssrf_entry_points_route_label():
    """O2 前半：join 命中 → source_endpoint 从 FuncBlock 占位变 "METHOD /path"，
    path 同步带前缀；miss → source_endpoint=None（F6a 去 handler-id 兜底）。"""
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow()], language_coverage=["python"],
    )

    ep = EntryPoint(
        func_block_id="app.py:proxy:1", entry_type="http_route",
        route="/proxy", http_method="GET", confidence=1.0,
        evidence="annot", needs_llm_review=False,
    )
    findings = await build_ssrf_findings(
        pgraph, verdict_agent=_agent(
            '{"verdict":"vulnerable","witness_payload":"http://127.0.0.1:22/",'
            '"evidence_chain":"url->fetch(L5)","mismatch_reason":"no allowlist",'
            '"confidence":"high"}'), entry_points={"app.py:proxy:1": ep})
    assert findings[0].source_endpoint == "GET /proxy"
    assert findings[0].path == "GET /proxy → url->fetch(L5)"

    # miss（不传 kwarg）→ source_endpoint=None（F6a：不再兜底 FuncBlock id
    # handler-id 脏值；缺失路由由 F6-B 白名单回填兜底）
    findings = await build_ssrf_findings(pgraph, verdict_agent=_agent(
            '{"verdict":"vulnerable","witness_payload":"http://127.0.0.1:22/",'
            '"evidence_chain":"url->fetch(L5)","mismatch_reason":"no allowlist",'
            '"confidence":"high"}'))
    assert findings[0].source_endpoint is None
    assert findings[0].path == "url->fetch(L5)"
