"""端到端:sanitizer 管道接通验证(防测试绿生产空转)。

从 mock LLM 返回的 TaintAnalysisResult(含 sanitized/sanitizer_description/
intermediate_vars/post_sanitized_concat)出发,经 analyze_taint_llm →
propagate_backward_across_chains → extract_candidate_chains → judge_chain_verdict,
断言 sanitizer 信息整条管道流通(非手动构造 step)。
"""
import pytest

from shannon_core.code_index.llm_taint_analyzer import analyze_taint_llm
from shannon_core.code_index.chain_propagator import propagate_backward_across_chains
from shannon_core.code_index.chain_verdict import extract_candidate_chains, judge_chain_verdict
from shannon_core.code_index.models import FuncBlock, CallChain, ParameterSource
from shannon_core.code_index.parameter_models import (
    DangerousSlot, SinkCallSite, SinkCategory, SlotContext,
    TaintAnalysisResult, TaintPath,
)


SINK_ID = "app.py:handler:db.execute:12:0"


def _handler_block():
    return FuncBlock(
        id="app.py:handler", function_name="handler", file_path="app.py",
        start_line=10, end_line=20, parameters=["q"], source_code="def handler(q): db.execute(q)",
        language="python",
    )


def _sink():
    return SinkCallSite(
        id=SINK_ID, caller_id="app.py:handler", callee_name="execute", callee_receiver="db",
        category=SinkCategory.SQL, sink_subtype="sql_raw_query", file_path="app.py",
        line=12, column=8,
        dangerous_slots=[DangerousSlot(arg_index=0, slot=SlotContext.SQL_VALUE,
                                       expression="'sel ' + q", is_entry_hint=True)],
        rule_id="py-sql-execute",
    )


def _source():
    from shannon_core.code_index.parameter_models import SourcePoint
    return SourcePoint(
        id="app.py:handler::q::10", entry_point_id="app.py:handler", param_name="q",
        source_type=ParameterSource.QUERY_PARAM, expression="req.query.q",
        file_path="app.py", line=10, rule_id="py-source-query",
    )


@pytest.mark.asyncio
async def test_sanitizer_pipeline_flows_end_to_end():
    """mock LLM 返回 sanitized=True → 全链 → chain_verdict prompt 含非空 sanitizer/expression。"""
    # 1. intra:mock LLM 返回带 sanitizer 的 TaintAnalysisResult
    async def taint_llm(prompt, **kw):
        import json
        return json.dumps({
            "tainted_params": ["q"],
            "propagation_paths": [{
                "source_param": "q", "sink_id": SINK_ID, "sink_arg_index": 0,
                "intermediate_vars": ["raw"], "sanitized": True,
                "sanitizer_description": "html.escape",
                "post_sanitized_concat": True, "confidence": 0.9,
            }],
        })

    intra = await analyze_taint_llm(
        _handler_block(), [_sink()], llm_client=taint_llm)

    # 防回退锚点 A:local_steps 非空(防 _intra_result_from_llm 再次丢弃)
    assert len(intra.local_steps) == 1, "intra 必须流出 summary step(断点 A 防回退)"
    assert "sanitize_hint:html.escape" in (intra.local_steps[0].transformation or "")

    # 2. propagate_backward(单函数场景)
    flows = propagate_backward_across_chains(
        chains=[CallChain(entry_point_id="app.py:handler", path=["app.py:handler"],
                          depth=0, has_unresolved=False)],
        blocks=[_handler_block()], intra_results={"app.py:handler": intra},
        sink_call_sites=[_sink()], source_points=[_source()],
    )
    assert len(flows) == 1
    # 防回退锚点 B:TaintFlow.propagation_steps 含 summary step(防 propagator 不合并)
    assert any(
        s.transformation and "sanitize_hint" in s.transformation
        for s in flows[0].propagation_steps
    ), "TaintFlow 必须含 intra summary step(断点 B 防回退)"

    # 3. extract + judge
    from shannon_core.code_index.parameter_models import ParameterPropagationGraph
    pgraph = ParameterPropagationGraph(taint_flows=flows, language_coverage=["python"])
    candidates = extract_candidate_chains(
        pgraph, vuln_class="injection", sink_call_sites={SINK_ID: _sink()})
    assert len(candidates) == 1
    c = candidates[0]
    assert c.sink_expressions == ["'sel ' + q"]              # expression 接入
    assert c.post_sanitize_concat is True                     # post_concat 标记识别
    assert c.sanitizer_annotations                            # annotate_sanitizers 匹配到(sanitize_library 不空转)

    captured = {}

    async def verdict_llm(prompt, **kw):
        captured["prompt"] = prompt
        import json
        return json.dumps({
            "verdict": "safe", "witness_payload": None,
            "evidence_chain": "q->db", "mismatch_reason": None, "confidence": "high",
        })

    verdict = await judge_chain_verdict(c, llm_client=verdict_llm)
    # 判定 LLM 拿到了完整信息(非空 sanitizer/expression/post_concat)
    assert "html.escape" in captured["prompt"]
    assert "'sel ' + q" in captured["prompt"]
    assert "raw" in captured["prompt"]
    assert "True" in captured["prompt"]   # post_sanitize_concat=True 进 prompt
    assert verdict.verdict == "safe"      # sanitizer 流通后 LLM 能判 safe(非机械 vulnerable)
