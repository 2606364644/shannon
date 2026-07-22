"""GitNexus taint-analysis 超时/解析失败走确定性兜底测试。

根因(2026-07-22 排查 sentinel_dashboard ssrf=0):
taint-analysis 阶段 per-function 粒度,SSRF 核心 sink 函数(proxyPprofRequest 等)
因 >60s 超时被 map_llm_with_bounds 跳过 -> 不进 intra_results -> backward 拿不到
seed -> 回退 dangerous_slots.expression(局部变量 "request") -> 无法映射到 caller
参数 -> source 锚定失败 -> 跨函数 SSRF flow 全丢。

修复(CLAUDE.md §1 "LLM 不可用/超时退回纯规则 + is_entry_hint,不浪费"):
- P0: 超时/异常跳过的 sink 函数走 _deterministic_intra_fallback 产兜底 IntraResult
      填入 intra_results(而非丢弃)。
- P1: LLM 返回响应但 parse_llm_response 解析失败时也 fallback(兑现 docstring
      "On LLM failure conservatively mark all params tainted",修 under-approximation)。
"""
import asyncio

from supernova_core.code_index.models import CallChain, FuncBlock, ParameterSource
from supernova_core.code_index.parameter_models import (
    DangerousSlot, IntraResult, SinkCallSite, SinkCategory, SlotContext, SourcePoint,
)
from supernova_core.code_index.chain_propagator import propagate_backward_across_chains
from supernova_core.code_index.llm_taint_analyzer import (
    _deterministic_intra_fallback, analyze_taint_llm,
)


def _blk(fid, source, params):
    fp, fn, ln = fid.split(":")
    return FuncBlock(id=fid, file_path=fp, function_name=fn, start_line=int(ln),
                     end_line=int(ln) + 5, source_code=source, parameters=params,
                     language="typescript")


def _ssrf_sink(caller_id, line=8):
    """SSRF sink,实参是局部变量 "request"(非函数参数, is_entry_hint=False)。

    复现 sentinel_dashboard PprofService.proxyPprofRequest 场景:
    httpClient.execute(request), request=new HttpGet(pprofUrl), pprofUrl=build(ip)。
    """
    return SinkCallSite(
        id=f"{caller_id}::execute::{line}:0", caller_id=caller_id, callee_name="execute",
        callee_receiver="httpClient", category=SinkCategory.SSRF, sink_subtype="ssrf_http_client",
        file_path="a.js", line=line, column=0,
        dangerous_slots=[DangerousSlot(
            arg_index=0, slot=SlotContext.URL, expression="request", is_entry_hint=False)],
        rule_id="java-httpclient-execute", needs_review=True,
    )


def _source(entry_id, param, expr):
    return SourcePoint(
        id=f"{entry_id}::{param}::1", entry_point_id=entry_id, param_name=param,
        source_type=ParameterSource.QUERY_PARAM, expression=expr, file_path="a.js", line=1,
        confidence=0.9, rule_id="ts-express-query",
    )


# ── P0: backfill_skipped_taint_fallback ──


def test_backfill_fills_skipped_func_with_deterministic_fallback():
    """超时/异常跳过的 sink 函数应被确定性兜底填入 intra_results(全参 tainted + 间接命中)。

    fail-first: 修复前该函数不存在;修复后 taint_items 全跳过时仍产出兜底 IntraResult,
    使 backward 有 seed 可用,不丢跨函数 flow(CLAUDE.md §1 "不浪费")。
    """
    from supernova_core.code_index import backfill_skipped_taint_fallback

    service = _blk("a.js:proxyPprofRequest:5",
                   "function proxyPprofRequest(app,ip,port,pprofPort){ httpClient.execute(request); }",
                   ["app", "ip", "port", "pprofPort"])
    sink = _ssrf_sink(service.id)
    taint_items = [(service.id, [sink])]
    taint_pairs = []  # 全部超时跳过(map_llm_with_bounds _Skip)
    blocks_by_id = {service.id: service}

    intra = backfill_skipped_taint_fallback(taint_items, taint_pairs, blocks_by_id)

    assert service.id in intra, "超时跳过的函数应被兜底填入 intra_results,而非丢弃"
    result = intra[service.id]
    assert result.tainted_params == {"app", "ip", "port", "pprofPort"}, \
        "兜底应保守标全部参数 tainted(保 backward chain seed)"
    assert sink.id in result.hits, "兜底应给 sink 间接命中(0.5, is_entry_hint=False)"
    assert result.hits[sink.id] == 0.5


def test_backfill_does_not_overwrite_already_analyzed():
    """已正常分析的函数(LLM 成功)不被兜底覆盖。"""
    from supernova_core.code_index import backfill_skipped_taint_fallback

    service = _blk("a.js:proxyPprofRequest:5", "function proxy(ip){}", ["ip", "port"])
    sink = _ssrf_sink(service.id)
    real_intra = IntraResult(tainted_params={"ip"}, hits={sink.id: 0.9}, local_steps=[])
    taint_items = [(service.id, [sink])]
    taint_pairs = [(service.id, real_intra)]  # 已分析
    blocks_by_id = {service.id: service}

    intra = backfill_skipped_taint_fallback(taint_items, taint_pairs, blocks_by_id)
    assert intra[service.id] is real_intra, "已分析的 IntraResult 必须保留,不被兜底覆盖"
    assert intra[service.id].tainted_params == {"ip"}


def test_backfill_skips_func_with_no_block():
    """block 找不到(已被裁剪)的 func_id 安全跳过,不崩。"""
    from supernova_core.code_index import backfill_skipped_taint_fallback

    sink = _ssrf_sink("a.js:gone:5")
    taint_items = [("a.js:gone:5", [sink])]
    taint_pairs = []
    intra = backfill_skipped_taint_fallback(taint_items, taint_pairs, blocks_by_id={})
    assert "a.js:gone:5" not in intra


# ── 传播层回归保护:兜底 IntraResult 是有效 seed ──


def test_deterministic_fallback_enables_backward_ssrf_flow_for_local_var_sink():
    """sink 实参局部变量(is_entry_hint=False)+ 跨函数(controller->service):
    当 service 的 intra 是确定性兜底(模拟 LLM 超时后 backfill 填入)时,
    backward 应产出 source->SSRF-sink 的跨函数 flow(不丢)。

    回归锚点:这是 sentinel_dashboard SSRF 漏报的核心场景。兜底 tainted_params=全部
    参数 -> backward _map_call_site_params_reverse 能把 ip/port 映射回 controller 的
    req.query.ip/port -> 锚定 SourcePoint -> 产 flow。
    """
    controller = _blk("a.js:getCpuProfile:1",
                      "function getCpuProfile(req){ proxyPprofRequest(req.query.ip, req.query.port); }",
                      ["req"])
    service = _blk("a.js:proxyPprofRequest:5",
                   "function proxyPprofRequest(ip,port){ var u=build(ip); var r=new Req(u); httpClient.execute(r); }",
                   ["ip", "port"])
    chain = CallChain(entry_point_id=controller.id, path=[controller.id, service.id],
                      depth=1, has_unresolved=False)
    sink = _ssrf_sink(service.id)
    # service 的 intra 走确定性兜底(等价于 backfill_skipped_taint_fallback 填入的结果)
    intra = {service.id: _deterministic_intra_fallback(service, [sink])}
    sps = [
        _source(controller.id, "ip", "req.query.ip"),
        _source(controller.id, "port", "req.query.port"),
    ]

    flows = propagate_backward_across_chains(
        [chain], [controller, service], intra, [sink], sps)

    assert len(flows) >= 1, "兜底 IntraResult 应让 backward 产出 SSRF 跨函数 flow(不丢)"
    assert all(f.sink_call_site_id == sink.id for f in flows)
    assert flows[0].sink_slot == SlotContext.URL, "backward flow 须透传 sink_slot(URL)"


def test_backward_drops_ssrf_flow_when_intra_missing_and_expression_is_local_var():
    """对照(根因重现):service 的 intra 完全缺失(超时跳过、未 backfill)且 sink 实参是
    局部变量 "request" 时,backward 丢 flow--这正是修复前的漏报状态。

    此测试 + 上一测试共同锁定:差异完全来自 intra 是否有兜底 seed。
    """
    controller = _blk("a.js:getCpuProfile:1",
                      "function getCpuProfile(req){ proxyPprofRequest(req.query.ip); }", ["req"])
    service = _blk("a.js:proxyPprofRequest:5",
                   "function proxyPprofRequest(ip){ httpClient.execute(request); }", ["ip"])
    chain = CallChain(entry_point_id=controller.id, path=[controller.id, service.id],
                      depth=1, has_unresolved=False)
    sink = _ssrf_sink(service.id)
    # intra 缺失 service(模拟超时跳过未 backfill 的修复前状态)
    intra = {}
    sps = [_source(controller.id, "ip", "req.query.ip")]

    flows = propagate_backward_across_chains(
        [chain], [controller, service], intra, [sink], sps)
    assert flows == [], "intra 缺失 + 局部变量实参 -> flow 丢(修复前 SSRF=0 的根因)"


# ── P1: analyze_taint_llm 解析失败走兜底 ──


def test_analyze_taint_llm_parse_failure_falls_back_to_deterministic():
    """LLM 返回非 JSON(parse 失败)时应 fallback 到确定性兜底(全参 tainted),
    而非返回空 tainted_params(under-approximation 违背 docstring "On LLM failure
    conservatively mark *all* parameters as tainted")。

    fail-first: 修复前解析失败静默返回空 tainted_params。
    """
    from unittest.mock import AsyncMock

    service = _blk("a.js:proxyPprofRequest:5",
                   "function proxyPprofRequest(app,ip,port,pprofPort){ httpClient.execute(request); }",
                   ["app", "ip", "port", "pprofPort"])
    sink = _ssrf_sink(service.id)
    # GLM 常返回带 markdown fence / 非 strict JSON 的输出 -> parse 失败
    llm_client = AsyncMock(return_value="```json\nnot actually json {{{\n```")

    result = asyncio.run(analyze_taint_llm(
        block=service, sinks_in_func=[sink], llm_client=llm_client))

    assert result.tainted_params == {"app", "ip", "port", "pprofPort"}, \
        "LLM 返回但解析失败时应 fallback 全参 tainted,而非空(避免 false negative)"
    assert sink.id in result.hits
