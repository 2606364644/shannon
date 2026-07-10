"""intra-first TaintFlow 单测(spec 2026-07-10 §3.2)。

不依赖 call chain,对每个含 sink 函数:取 intra.local_steps + tainted_params,
_source_points_matching 推广到 sink 所在函数 → 匹配 SourcePoint 直接产 TaintFlow。
覆盖 NodeGoat handler 不在 chain → propagate_backward 丢弃 intra 结果的根因(§2)。
"""
from shannon_core.code_index.models import FuncBlock, ParameterSource
from shannon_core.code_index.parameter_models import (
    DangerousSlot, IntraResult, PropagationStep, SinkCallSite, SinkCategory,
    SlotContext, SourcePoint, TaintFlow,
)
from shannon_core.code_index.chain_propagator import (
    merge_taint_flows,
    produce_intra_first_taint_flows,
)


def _blk(fid, source, params):
    fp, fn, ln = fid.split(":")
    return FuncBlock(id=fid, file_path=fp, function_name=fn, start_line=int(ln),
                     end_line=int(ln) + 5, source_code=source, parameters=params,
                     language="typescript")


def _sink(caller_id, line=2, dangerous_slots=None):
    return SinkCallSite(
        id=f"{caller_id}::eval::{line}:0", caller_id=caller_id, callee_name="eval",
        callee_receiver=None, category=SinkCategory.COMMAND, sink_subtype="command_eval",
        file_path="a.js", line=line, column=0,
        dangerous_slots=dangerous_slots or [
            DangerousSlot(arg_index=0, slot=SlotContext.CMD_ARGUMENT,
                          expression="req.body.preTax", is_entry_hint=True),
        ],
        rule_id="ts-eval", needs_review=False,
    )


def _source(entry_id, param, stype, expr, rule_id="ts-express-body"):
    return SourcePoint(
        id=f"{entry_id}::{param}::1", entry_point_id=entry_id, param_name=param,
        source_type=stype, expression=expr, file_path="a.js", line=1,
        confidence=0.9, rule_id=rule_id,
    )


def _intra(func_id, sink_id, tainted_param="req", with_step=True):
    steps = []
    if with_step:
        steps.append(PropagationStep(
            from_func_id=func_id, from_param=tainted_param, to_func_id=func_id,
            to_param=sink_id, code_location="a.js:2", confidence=0.9,
        ))
    return IntraResult(tainted_params={tainted_param}, hits={sink_id: 0.9},
                       local_steps=steps)


def test_intra_first_produces_flow_for_sink_func_not_in_chain():
    """含 sink 函数(handler)不在任何 chain → propagate_backward 漏;intra-first 直接产 TaintFlow。

    回归锚点(spec §2 根因):NodeGoat ContributionsHandler 不在 chain,propagate_backward
    丢弃 intra 结果 → taint_flows=0;intra-first 不依赖 chain,同函数 source→sink 直接产 flow。
    """
    handler = _blk("a.js:handler:1",
                   "function handler(req){ eval(req.body.preTax); }", ["req"])
    sink = _sink(handler.id)
    intra = {handler.id: _intra(handler.id, sink.id, tainted_param="req")}
    sp = _source(handler.id, "preTax", ParameterSource.BODY_FIELD, "req.body.preTax")

    flows = produce_intra_first_taint_flows(
        sink_call_sites=[sink], intra_results=intra,
        source_points=[sp], blocks=[handler])

    assert len(flows) == 1
    f = flows[0]
    assert f.sink_call_site_id == sink.id
    assert f.entry_point_id == handler.id  # source anchor 到含 sink 函数
    assert f.source_param == "preTax"
    assert f.source_type == ParameterSource.BODY_FIELD
    assert f.notes == "intra-first"
    # 单步 intra(local_steps 该 sink 的 step)
    assert any(s.to_param == sink.id for s in f.propagation_steps)
    # sink_slot 透传(防 _route_for 拒 inj/ssrf,同 backward)
    assert f.sink_slot == SlotContext.CMD_ARGUMENT
    # 规则 source(非 llm-discovered)→ needs_review=False
    assert f.needs_review is False


def test_intra_first_marks_needs_review_for_llm_discovered_source():
    """source 是 llm-discovered-source(软)→ TaintFlow.needs_review=True(下游 chain_verdict 复核)。"""
    handler = _blk("a.js:handler:1",
                   "function handler(req){ eval(req.body.preTax); }", ["req"])
    sink = _sink(handler.id)
    intra = {handler.id: _intra(handler.id, sink.id, tainted_param="req")}
    sp = _source(handler.id, "preTax", ParameterSource.BODY_FIELD, "req.body.preTax",
                 rule_id="llm-discovered-source")

    flows = produce_intra_first_taint_flows(
        [sink], intra, [sp], [handler])

    assert len(flows) == 1
    assert flows[0].needs_review is True


def test_intra_first_skips_sink_not_in_intra_hits():
    """intra 没判定该 sink 命中(hits 不含)→ 不产 TaintFlow。"""
    handler = _blk("a.js:handler:1", "function handler(req){ eval(req.body.x); }", ["req"])
    sink = _sink(handler.id)
    intra = {handler.id: IntraResult(tainted_params={"req"}, hits={},  # 该 sink 未命中
                                     local_steps=[])}
    sp = _source(handler.id, "x", ParameterSource.BODY_FIELD, "req.body.x")

    flows = produce_intra_first_taint_flows([sink], intra, [sp], [handler])
    assert flows == []


def test_intra_first_skips_when_no_source_match():
    """含 sink 函数但无 SourcePoint 匹配(没补召回 source)→ 不产。"""
    handler = _blk("a.js:handler:1", "function handler(req){ eval(req.body.x); }", ["req"])
    sink = _sink(handler.id)
    intra = {handler.id: _intra(handler.id, sink.id, tainted_param="req")}

    flows = produce_intra_first_taint_flows([sink], intra, [], [handler])  # 无 source
    assert flows == []


def test_merge_taint_flows_intra_first_priority():
    """intra-first + backward 同 (entry, param, sink) → intra-first 优先,backward 去重掉。

    同函数场景:handler 既是 chain entry 又含 sink → intra-first 和 backward 都产
    (handler::preTax, sink);合并去重,intra-first 优先(spec §3.2)。
    """
    handler = _blk("a.js:handler:1", "...", ["req"])
    sink = _sink(handler.id)
    common = dict(entry_point_id=handler.id, source_param="preTax",
                  source_type=ParameterSource.BODY_FIELD, sink_call_site_id=sink.id)
    intra_flow = TaintFlow(flow_id="intra", notes="intra-first", **common)
    backward_flow = TaintFlow(flow_id="back", notes="backward-anchored", **common)

    merged = merge_taint_flows([intra_flow], [backward_flow])
    assert len(merged) == 1
    assert merged[0].notes == "intra-first"  # intra-first 优先保留


def test_merge_keeps_cross_function_backward_flow():
    """backward 产的跨函数 flow(entry ≠ sink_func)与 intra-first 不同 key → 保留(不重复)。"""
    handler = _blk("a.js:handler:1", "...", ["req"])
    sink = _sink(handler.id)
    # intra-first:同函数(source 在 handler)
    intra_flow = TaintFlow(
        flow_id="f1", entry_point_id=handler.id, source_param="preTax",
        source_type=ParameterSource.BODY_FIELD, sink_call_site_id=sink.id,
        notes="intra-first",
    )
    # backward:跨函数(source 在别的 entry,但 sink 同)—— 不同 entry_point_id
    backward_flow = TaintFlow(
        flow_id="f2", entry_point_id="a.js:otherEntry:1", source_param="q",
        source_type=ParameterSource.QUERY_PARAM, sink_call_site_id=sink.id,
        notes="backward-anchored",
    )

    merged = merge_taint_flows([intra_flow], [backward_flow])
    assert len(merged) == 2  # 两条都保留(不同 entry/source)
    notes = {f.notes for f in merged}
    assert notes == {"intra-first", "backward-anchored"}


def test_pipeline_intra_first_rescues_handler_not_in_entry_point():
    """端到端 NodeGoat 锚点(spec §5):handler 含 eval(req.body.preTax)但不在
    entry_point(detect_entry_points 把路由归注册处)→ detect_sources 主路径漏,
    source 补召回(对含 sink 函数)+ intra-first 产 TaintFlow(当前 0,修复后 >0)。

    llm_client=None 模拟关 LLM 轨(SHANNON_LLM_TRACK_ENABLED=0 + GitNexus LLM 不可用)
    → analyze_taint_llm 走确定性 fallback(tainted_params=全部参数,守召回)。
    """
    import asyncio
    import os
    import tempfile
    from unittest.mock import AsyncMock, patch

    from shannon_core.code_index import build_code_index_with_gitnexus

    with tempfile.TemporaryDirectory() as repo:
        with open(os.path.join(repo, "app.js"), "w") as fh:
            fh.write(
                "function handler(req, res){\n"
                "  eval(req.body.preTax);\n"
                "}\n"
            )
        os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
        fake_mcp = AsyncMock()
        fake_mcp.call_tool = AsyncMock(return_value={"upstream": [], "downstream": []})
        with patch("shannon_core.code_index.detect_entry_points", return_value=[]):
            index, _rg, _sg = asyncio.run(build_code_index_with_gitnexus(
                repo, mcp_client=fake_mcp, llm_client=None))  # 关 LLM → 确定性 fallback

        # source 补召回补回 req.body.preTax(handler 含 eval sink,但不在 entry_point)
        assert any(sp.param_name == "preTax" for sp in index.source_points), \
            f"source 补召回应补回 handler 的 req.body.preTax, got {index.source_points}"
        # intra-first 产 TaintFlow(同函数 source→sink,不经 chain;NodeGoat 全空根因修复)
        assert len(index.parameter_graph.taint_flows) > 0, \
            f"intra-first 应对 handler 产 TaintFlow, got {index.parameter_graph.taint_flows}"
