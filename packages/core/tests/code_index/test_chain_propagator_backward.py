from shannon_core.code_index.models import CallChain, FuncBlock, ParameterSource
from shannon_core.code_index.parameter_models import (
    DangerousSlot, IntraResult, PropagationStep, SinkCallSite, SinkCategory, SlotContext,
    SourcePoint, TaintFlow,
)
from shannon_core.code_index.chain_propagator import (
    _map_call_site_params_reverse,
    propagate_backward_across_chains,
)


def _blk(fid, source, params):
    fp, fn, ln = fid.split(":")
    return FuncBlock(id=fid, file_path=fp, function_name=fn, start_line=int(ln),
                     end_line=int(ln)+5, source_code=source, parameters=params,
                     language="typescript")


def test_reverse_map_propagates_tainted_callee_param_to_caller_arg():
    # caller 调用 callee(taintedParam),实参 req.query.x → caller 端 tainted = {req.query.x}
    caller = _blk("a.js:handler:1",
                  "function handler(req){ callee(req.query.x); }", ["req"])
    callee = _blk("a.js:callee:5",
                  "function callee(taintedParam){ eval(taintedParam); }",
                  ["taintedParam"])
    out = _map_call_site_params_reverse(
        callee_block=callee, callee_tainted={"taintedParam"}, caller_block=caller)
    assert out == {"req.query.x"}  # caller 传入的实参表达式


def test_reverse_map_empty_when_callee_param_not_tainted():
    caller = _blk("a.js:handler:1", "function handler(req){ callee('literal'); }", ["req"])
    callee = _blk("a.js:callee:5", "function callee(p){}", ["p"])
    out = _map_call_site_params_reverse(
        callee_block=callee, callee_tainted={"p"}, caller_block=caller)
    # 实参 'literal' 不引用 tainted → 空
    assert out == set()


def test_reverse_map_conservative_when_no_call_args_found():
    caller = _blk("a.js:handler:1", "function handler(req){ /* no call */ }", ["req"])
    callee = _blk("a.js:callee:5", "function callee(p){}", ["p"])
    out = _map_call_site_params_reverse(
        callee_block=callee, callee_tainted={"p"}, caller_block=caller)
    # 找不到调用实参 → 保守:caller 所有 params 视为 tainted
    assert out == {"req"}


def _sink(caller_id, line=10, dangerous_slots=None):
    return SinkCallSite(
        id=f"{caller_id}::eval::{line}:0", caller_id=caller_id, callee_name="eval",
        callee_receiver=None, category=SinkCategory.COMMAND, sink_subtype="command_eval",
        file_path="a.js", line=line, column=0,
        dangerous_slots=dangerous_slots or [], rule_id="ts-eval", needs_review=False,
    )


def _source(entry_id, param, stype, expr):
    return SourcePoint(
        id=f"{entry_id}::{param}::1", entry_point_id=entry_id, param_name=param,
        source_type=stype, expression=expr, file_path="a.js", line=1,
        confidence=0.9, rule_id="ts-express-query",
    )


def test_backward_anchor_succeeds_when_sink_reaches_sourcepoint():
    # chain: handler(entry) → callee(含 eval sink)
    handler = _blk("a.js:handler:1",
                   "function handler(req){ callee(req.query.x); }", ["req"])
    callee = _blk("a.js:callee:5",
                  "function callee(p){ eval(p); }", ["p"])
    chain = CallChain(entry_point_id=handler.id, path=[handler.id, callee.id],
                      depth=1, has_unresolved=False)
    # callee 的 intra:tainted_params={p}, hits={sink_id: conf}
    sink = _sink(callee.id, line=6)
    intra = {
        handler.id: IntraResult(tainted_params={"req.query.x"}, hits={}),
        callee.id: IntraResult(tainted_params={"p"}, hits={sink.id: 0.9}),
    }
    sps = [_source(handler.id, "x", ParameterSource.QUERY_PARAM, "req.query.x")]
    flows = propagate_backward_across_chains(
        [chain], [handler, callee], intra, [sink], sps)
    assert len(flows) == 1
    assert isinstance(flows[0], TaintFlow)
    assert flows[0].sink_call_site_id == sink.id
    assert flows[0].source_type == ParameterSource.QUERY_PARAM  # 精确,非硬编码


def test_backward_drops_chain_when_no_sourcepoint_anchor():
    # sink 存在但反向追不到任何 SourcePoint(entry 无可控 source)→ 丢弃
    handler = _blk("a.js:handler:1",
                   "function handler(req){ callee('safe_literal'); }", ["req"])
    callee = _blk("a.js:callee:5",
                  "function callee(p){ eval(p); }", ["p"])
    chain = CallChain(entry_point_id=handler.id, path=[handler.id, callee.id],
                      depth=1, has_unresolved=False)
    sink = _sink(callee.id, line=6)
    intra = {
        handler.id: IntraResult(tainted_params=set(), hits={}),
        callee.id: IntraResult(tainted_params={"p"}, hits={sink.id: 0.9}),
    }
    flows = propagate_backward_across_chains(
        [chain], [handler, callee], intra, [sink], [])  # 无 SourcePoint
    assert flows == []  # 双向锚定:无 source 锚 → 丢弃


def test_pipeline_uses_backward_for_taint_flows():
    """pipeline 冒烟:taint_flows 由 propagate_backward 产(含 source_type 精确)。"""
    import asyncio
    from unittest.mock import AsyncMock
    from shannon_core.code_index import build_code_index_with_gitnexus
    import tempfile, os

    with tempfile.TemporaryDirectory() as repo:
        with open(os.path.join(repo, "app.js"), "w") as fh:
            # 复用 A4 的 fixture 模式:route 在 setupRoutes 函数体内(Express Pass1 命中真 block)
            fh.write(
                "function setupRoutes(app){\n"
                "  app.get('/r', function h(req){ sink(req.query.x); });\n"
                "}\n"
                "function sink(p){ eval(p); }\n"
            )
        os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
        fake_mcp = AsyncMock()
        fake_mcp.call_tool = AsyncMock(return_value={"upstream": [], "downstream": []})
        fake_llm = AsyncMock(return_value="[]")
        index, _rg, _sg, _sg2 = asyncio.run(build_code_index_with_gitnexus(
            repo, mcp_client=fake_mcp, llm_client=fake_llm))
        # 至少有 source_point(req.query.x);若 sink 被 sink_detector 规则命中,
        # backward 应产 TaintFlow(具体取决于规则覆盖;此测试验证不崩 + source_points 非空)
        assert any(sp.param_name == "x" for sp in index.source_points)


# ── Task 3: intra local_steps 合并进 TaintFlow.propagation_steps ──


def _intra_with_sanitizer(func_id, sink_id, tainted_param="p"):
    """intra 产出一个带 sanitizer 的 summary step(Task 2 的产物形态)。"""
    return IntraResult(
        tainted_params={tainted_param},
        hits={sink_id: 0.9},
        local_steps=[PropagationStep(
            from_func_id=func_id, from_param=tainted_param, to_func_id=func_id,
            to_param=sink_id,
            transformation="sanitize_hint:html.escape|post_concat",
            code_location="a.js:6", intermediate_vars=["raw"], confidence=0.9,
        )],
    )


def test_backward_merges_intra_sanitizer_step_single_function():
    """单函数注入(sink 在 entry,sink_step=0):flow.propagation_steps 必须含 intra summary step。

    回归锚点:之前 sink_step=0 时 steps_fwd=[] → sanitizer 流不通(最常见注入场景)。
    """
    handler = _blk("a.js:handler:1", "function handler(req){ eval(req.query.x); }", ["req"])
    sink = _sink(handler.id, line=2)
    chains = [CallChain(entry_point_id=handler.id, path=[handler.id],
                        depth=0, has_unresolved=False)]
    # tainted_params 必须含 source_point.expression 的子串才能锚定
    intra = {handler.id: _intra_with_sanitizer(handler.id, sink.id, tainted_param="req.query.x")}

    flows = propagate_backward_across_chains(
        chains=chains, blocks=[handler], intra_results=intra,
        sink_call_sites=[sink],
        source_points=[_source(handler.id, "x", ParameterSource.QUERY_PARAM, "req.query.x")],
    )
    assert len(flows) == 1
    steps = flows[0].propagation_steps
    assert any(s.transformation and "sanitize_hint:html.escape" in s.transformation for s in steps), \
        "单函数场景下 intra summary step 必须被合并进 TaintFlow"


def test_backward_merges_intra_sanitizer_step_multi_function():
    """多函数(entry→sink_func):跨函数 hop + sink_func 内 summary step 都在。"""
    entry = _blk("a.js:entry:1", "function entry(req){ db(req.query.x); }", ["req"])
    callee = _blk("a.js:db:5", "function db(p){ eval(p); }", ["p"])
    sink = _sink(callee.id, line=6)
    chains = [CallChain(entry_point_id=entry.id, path=[entry.id, callee.id],
                        depth=1, has_unresolved=False)]
    intra = {callee.id: _intra_with_sanitizer(callee.id, sink.id)}

    flows = propagate_backward_across_chains(
        chains=chains, blocks=[entry, callee], intra_results=intra,
        sink_call_sites=[sink],
        source_points=[_source(entry.id, "x", ParameterSource.QUERY_PARAM, "req.query.x")],
    )
    assert len(flows) == 1
    steps = flows[0].propagation_steps
    # 既有跨函数 hop,又有带 sanitizer 的 summary step
    assert any(s.transformation and "sanitize_hint" in s.transformation for s in steps)


# ── Task 3 fix: sink_slot / tainted_arg_index 透传(防 _route_for 拒 inj/ssrf) ──


def test_backward_propagates_sink_slot_and_arg_index():
    """sink 带 DangerousSlot(SQL_VALUE, arg_index=0) → TaintFlow.sink_slot 必须等于 SQL_VALUE,
    tainted_arg_index 必须等于 0。

    回归锚点:之前 backward 构造 TaintFlow 时不设 sink_slot/tainted_arg_index,
    模型默认 GENERIC/-1,而 _route_for(injection) 的 _INJECTION_SLOTS 不含 generic →
    backward 构造的 inj/ssrf flow 全部被 extract_candidate_chains 拒掉,
    sanitizer 信息到不了 verdict prompt(NodeGoat injection 漏盘的真因之一)。
    """
    handler = _blk("a.js:handler:1", "function handler(req){ eval(req.query.x); }", ["req"])
    sink = _sink(handler.id, line=2, dangerous_slots=[
        DangerousSlot(arg_index=0, slot=SlotContext.SQL_VALUE,
                      expression="req.query.x", is_entry_hint=True),
    ])
    chains = [CallChain(entry_point_id=handler.id, path=[handler.id],
                        depth=0, has_unresolved=False)]
    intra = {handler.id: _intra_with_sanitizer(handler.id, sink.id, tainted_param="req.query.x")}

    flows = propagate_backward_across_chains(
        chains=chains, blocks=[handler], intra_results=intra,
        sink_call_sites=[sink],
        source_points=[_source(handler.id, "x", ParameterSource.QUERY_PARAM, "req.query.x")],
    )
    assert len(flows) == 1
    # 关键:slot/arg_index 从 sink.dangerous_slots[0] 透传到 flow
    assert flows[0].sink_slot == SlotContext.SQL_VALUE, \
        f"backward flow 必须透传 sink_slot(否则 _route_for 拒 inj/ssrf);got {flows[0].sink_slot!r}"
    assert flows[0].tainted_arg_index == 0, \
        f"backward flow 必须透传 tainted_arg_index;got {flows[0].tainted_arg_index!r}"


def test_backward_sink_slot_defaults_to_generic_when_no_dangerous_slots():
    """sink.dangerous_slots 空 → 行为不变(GENERIC/-1),保持向后兼容。"""
    handler = _blk("a.js:handler:1", "function handler(req){ eval(req.query.x); }", ["req"])
    sink = _sink(handler.id, line=2)  # dangerous_slots=[] (default)
    chains = [CallChain(entry_point_id=handler.id, path=[handler.id],
                        depth=0, has_unresolved=False)]
    intra = {handler.id: _intra_with_sanitizer(handler.id, sink.id, tainted_param="req.query.x")}

    flows = propagate_backward_across_chains(
        chains=chains, blocks=[handler], intra_results=intra,
        sink_call_sites=[sink],
        source_points=[_source(handler.id, "x", ParameterSource.QUERY_PARAM, "req.query.x")],
    )
    assert len(flows) == 1
    # 向后兼容:空 slots → 默认值
    assert flows[0].sink_slot == SlotContext.GENERIC
    assert flows[0].tainted_arg_index == -1
