"""sink_discovery_llm 单测 — 半 sink 收集 + LLM 补召回(spec 方案 A)."""
import json

import pytest

from shannon_core.code_index.models import FuncBlock
from shannon_core.code_index.parameter_models import SinkCategory, SlotContext
from shannon_core.code_index.sink_discovery_llm import (
    SuspiciousCall,
    collect_suspicious_calls,
    discover_sinks_llm,
    RuleGap,
)


class _FakeCall:
    def __init__(self, line, column=0):
        self.line = line
        self.column = column


class _FakeParser:
    """记录预设的 call → (callee, receiver, arg_exprs), 命中 sink_detector 规则的用真规则名."""
    def __init__(self, calls):
        self._calls = calls  # [(callee, receiver, arg_exprs, line), ...]

    def iter_calls(self, block, source):
        return [_FakeCall(line) for *_, line in self._calls]

    def destructure_call(self, call):
        callee, receiver, _, line = self._calls[call.line - 1]
        return callee, receiver

    def extract_arg_expressions(self, call, source):
        _, _, arg_exprs, _ = self._calls[call.line - 1]
        return arg_exprs


def _block(name="handler", file="app.py", language="python", source="def handler(): pass"):
    return FuncBlock(
        id=f"{file}:{name}:1", file_path=file, function_name=name,
        start_line=1, end_line=10, source_code=source,
        parameters=["uid"], language=language,
    )


def test_collects_sinkish_unmatched_call():
    # raw_query 是 sink-ish(query) 但规则库无 raw_query@custom_db → 收集
    block = _block()
    parser = _FakeParser([("raw_query", "custom_db", ["\"SELECT \" + uid"], 1)])
    out = collect_suspicious_calls([block], parser, source_provider=lambda b: b"src")
    assert len(out) == 1
    assert out[0].callee == "raw_query"
    assert out[0].receiver == "custom_db"


def test_skips_rule_hit_call():
    # cursor.execute 命中 py-db-cursor-execute 规则 → 不收集
    block = _block()
    parser = _FakeParser([("execute", "cursor", ["uid"], 1)])
    out = collect_suspicious_calls([block], parser, source_provider=lambda b: b"src")
    assert out == []


def test_skips_non_sinkish_call():
    block = _block()
    parser = _FakeParser([("helper", None, ["uid"], 1)])
    out = collect_suspicious_calls([block], parser, source_provider=lambda b: b"src")
    assert out == []


def _suspicious(callee="raw_query", receiver="custom_db", line=1, arg="uid"):
    from shannon_core.code_index.models import FuncBlock
    block = FuncBlock(
        id=f"app.py:handler:{line}", file_path="app.py", function_name="handler",
        start_line=1, end_line=10, source_code="def handler(): pass",
        parameters=["uid"], language="python",
    )
    return SuspiciousCall(block=block, callee=callee, receiver=receiver,
                          arg_exprs=[arg], file_path="app.py", line=line, column=0)


async def test_discover_produces_soft_sink(monkeypatch):
    # LLM 判 is_sink=True → 软 SinkCallSite, rule_id=llm-discovered, needs_review=True
    async def client(prompt, **kw):
        return json.dumps([{"call_ref": "raw_query:1", "is_sink": True,
                            "category": "sql", "slot": "sql_value", "arg_index": 0,
                            "rationale": "raw SQL concat"}])
    soft, gaps = await discover_sinks_llm([_suspicious()], client)
    assert len(soft) == 1
    s = soft[0]
    assert s.rule_id == "llm-discovered"
    assert s.needs_review is True
    assert s.category == SinkCategory.SQL
    assert s.dangerous_slots[0].slot == SlotContext.SQL_VALUE
    assert s.dangerous_slots[0].arg_index == 0


async def test_discover_skips_non_sink(monkeypatch):
    async def client(prompt, **kw):
        return json.dumps([{"call_ref": "raw_query:1", "is_sink": False}])
    soft, gaps = await discover_sinks_llm([_suspicious()], client)
    assert soft == []


async def test_discover_degrades_when_llm_unavailable():
    # llm_client=None → 返回空(降级), 不抛
    soft, gaps = await discover_sinks_llm([_suspicious()], None)
    assert soft == [] and gaps == []

    async def raising(prompt, **kw):
        raise RuntimeError("timeout")
    soft, gaps = await discover_sinks_llm([_suspicious()], raising)
    assert soft == [] and gaps == []


async def test_gap_aggregation():
    # 同 pattern 的两个软 sink → 聚合成 1 条 gap, count=2
    async def client(prompt, **kw):
        return json.dumps([
            {"call_ref": "raw_query:1", "is_sink": True, "category": "sql",
             "slot": "sql_value", "arg_index": 0, "rationale": "x"},
        ])
    calls = [_suspicious(line=1), _suspicious(line=2)]
    soft, gaps = await discover_sinks_llm(calls, client)
    assert len(gaps) == 1
    assert gaps[0].count == 1  # client 每次只判 1 个 → gap 聚合按实际产出的软 sink
    # 补一个真两软 sink 的场景:
    async def client2(prompt, **kw):
        return json.dumps([
            {"call_ref": "raw_query:1", "is_sink": True, "category": "sql",
             "slot": "sql_value", "arg_index": 0, "rationale": "x"},
            {"call_ref": "raw_query:2", "is_sink": True, "category": "sql",
             "slot": "sql_value", "arg_index": 0, "rationale": "y"},
        ])
    calls2 = [_suspicious(line=1), _suspicious(line=2)]
    soft2, gaps2 = await discover_sinks_llm(calls2, client2)
    assert len(soft2) == 2
    assert len(gaps2) == 1
    assert gaps2[0].count == 2
    assert gaps2[0].pattern == "raw_query@custom_db"


async def test_soft_sink_flows_into_intra_hits():
    """软 sink 并入 sinks_by_func → analyze_taint_llm 能对其产 hits(集成 smoke)."""
    from collections import defaultdict
    from shannon_core.code_index.parameter_models import IntraResult
    from shannon_core.code_index import chain_propagator  # 仅验类型可达, 实际用 mock

    # 构造一个软 sink + 一个有它作 sink 的 block, 验证它进 sinks_by_func 后
    # analyze_taint_llm 的确定性 fallback 能命中它(is_entry_hint 或 indirect)。
    sc = _suspicious(arg="uid")  # uid 是参数 → is_entry_hint=True
    async def client(prompt, **kw):
        return json.dumps([{"call_ref": "raw_query:1", "is_sink": True,
                            "category": "sql", "slot": "sql_value", "arg_index": 0,
                            "rationale": "x"}])
    soft, gaps = await discover_sinks_llm([sc], client)
    assert soft and soft[0].dangerous_slots[0].is_entry_hint is True  # uid 是参数

    # 模拟 build 函数的 sinks_by_func 分组 + 确定性 intra
    sinks_by_func = defaultdict(list)
    for s in soft:
        sinks_by_func[s.caller_id].append(s)
    from shannon_core.code_index.llm_taint_analyzer import _deterministic_intra_fallback
    intra = _deterministic_intra_fallback(sc.block, sinks_by_func[sc.block.id])
    assert soft[0].id in intra.hits  # 软 sink 被 intra 命中 → 会进 TaintFlow


async def test_soft_sink_does_not_break_injection_whitelist():
    """行为锁定(spec §6 Task 6 fix): 一个 rule_id="llm-discovered" 的软 sink,
    当其 TaintFlow.sink_slot ∈ _INJECTION_SLOTS 时, 必须完整穿过
    injection_builder → 产出 InjectionVulnerability, 且 finding 的身份
    (sink_call) 能追溯回该软 sink —— 即软 sink 没有被下游任何过滤丢弃.

    本测试做的是行为校验, 不是 source-text / 结构断言:
      1. 经 discover_sinks_llm 真实产出一个软 SinkCallSite(rule_id=llm-discovered);
      2. 把它的 id 作为 TaintFlow.sink_call_site_id, slot=SQL_VALUE, 构造
         一个最小 ParameterPropagationGraph;
      3. 跑 extract_candidate_chains → 软 sink 链被路由进 injection 桶;
      4. 跑 build_injection_findings(mock verdict=vulnerable) → 软 sink
         产出 InjectionVulnerability 且 sink_call 指回软 sink id.

    任何下游路径若因 rule_id="llm-discovered" / 新 sink_subtype 而丢弃该链,
    本测试会 FAIL —— 这正是 spec 要锁的不变量.

    (旧版的 source-text/白名单维度断言已删除: comparing sink_subtype against
    issue_type strings 是 non-falsifiable, 锁不到运行时行为.)"""
    from shannon_core.code_index.parameter_models import (
        ParameterPropagationGraph,
        ParameterSource,
        TaintFlow,
    )
    from shannon_core.code_index.chain_verdict import (
        _INJECTION_SLOTS,
        extract_candidate_chains,
    )
    from shannon_core.code_index.vuln_chain_builders.injection_builder import (
        build_injection_findings,
    )

    # 1) 真实产出软 sink(rule_id="llm-discovered", SQL_VALUE 槽).
    sc = _suspicious(arg="uid")
    async def discover_client(prompt, **kw):
        return json.dumps([{"call_ref": "raw_query:1", "is_sink": True,
                            "category": "sql", "slot": "sql_value", "arg_index": 0,
                            "rationale": "x"}])
    soft, _ = await discover_sinks_llm([sc], discover_client)
    assert len(soft) == 1
    soft_sink = soft[0]
    assert soft_sink.rule_id == "llm-discovered"          # 软 sink 标记
    assert soft_sink.dangerous_slots[0].slot == SlotContext.SQL_VALUE
    assert "sql_value" in _INJECTION_SLOTS                # 该槽位确实归 injection

    # 2) 构造最小 pgraph: 一条 TaintFlow 终点指向该软 sink.
    pgraph = ParameterPropagationGraph(
        taint_flows=[TaintFlow(
            flow_id=f"ep#1->{soft_sink.id}",
            entry_point_id="ep#1",
            source_param="uid",
            source_type=ParameterSource.QUERY_PARAM,
            sink_call_site_id=soft_sink.id,
            sink_slot=SlotContext.SQL_VALUE,
        )],
        language_coverage=["python"],
    )

    # 3) 路由层: 软 sink 链必须被 extract_candidate_chains 抽出来(不被 slot
    #    /subtype/rule_id 过滤掉). 若任何下游因 rule_id 把它过滤, 这里 FAIL.
    chains = extract_candidate_chains(pgraph, vuln_class="injection")
    assert len(chains) == 1, f"soft-sink chain dropped at routing: {chains}"
    assert chains[0].sink_call_site_id == soft_sink.id
    assert chains[0].sink_slot == "sql_value"

    # 4) 端到端: build_injection_findings 走完 verdict, 必须产出 1 条 finding,
    #    且 finding.sink_call 指回软 sink id —— 证明软 sink 没被 builder 丢弃.
    async def verdict_client(prompt, ** kw):
        return json.dumps({"verdict": "vulnerable", "confidence": "high",
                           "evidence_chain": "uid -> soft_sink",
                           "witness_payload": "w", "mismatch_reason": "r"})
    findings = await build_injection_findings(pgraph, llm_client=verdict_client)
    assert len(findings) == 1, f"soft-sink finding dropped by builder: {findings}"
    f = findings[0]
    assert f.vulnerability_type == "injection"
    assert f.source_track == "gitnexus"
    # 关键断言: finding 身份(sink_call)能追溯回软 sink —— 没被任何过滤丢弃.
    assert f.sink_call == soft_sink.id
    assert f.verdict == "vulnerable"


async def test_discover_partial_failure_keeps_successful_sinks():
    """并发改造(治本2):部分函数 LLM 挂死(超时)→ 被跳过,成功函数仍产 soft sink。

    两个不同 block 的 suspicious(raw_query + exec_one),并发跑;raw_query 函数
    正常返回,exec_one 函数挂死 → per_call_timeout 砍掉它。成功的 raw_query
    soft sink 必须保留(不被并发的失败项带垮)。
    """
    import asyncio
    calls = [
        _suspicious(line=1, callee="raw_query"),
        _suspicious(line=2, callee="exec_one"),
    ]

    async def client(prompt, **kw):
        if "raw_query:1" in prompt:
            return json.dumps([{"call_ref": "raw_query:1", "is_sink": True,
                                "category": "sql", "slot": "sql_value",
                                "arg_index": 0, "rationale": "x"}])
        await asyncio.sleep(10)  # exec_one 挂死

    soft, _ = await discover_sinks_llm(
        calls, client, concurrency=2, per_call_timeout=0.2)
    assert len(soft) == 1
    assert soft[0].callee_name == "raw_query"


async def test_discover_sinks_llm_reports_progress_and_hits():
    """T4: progress_cb 接进来后,per-function tick + 末尾 finalize 均上报。

    复用文件内已有的 `_suspicious` helper(brief 占位名 `_build_two_suspicious_calls`
    在本文件不存在;`_suspicious` 每次构造的 block.id 含 line,故 line 不同的两个
    suspicious 分属 2 个函数)。构造 2 个函数(raw_query 命中 sink / exec_one 未命中),
    断言:
      - 至少一条 sample 带非空 detail(命中 tick)
      - 末尾 sample.final=True 且汇总文案含软 sink 计数
      - done == 去重函数数
    """
    from shannon_core.code_index.progress import ProgressSample

    calls = [
        _suspicious(line=1, callee="raw_query"),
        _suspicious(line=2, callee="exec_one"),
    ]
    samples: list[ProgressSample] = []

    async def client(prompt, **kw):
        # 每个函数的 prompt 只含它自己的 call_ref;按 call_ref 返判定。
        if "raw_query:1" in prompt:
            return json.dumps([{"call_ref": "raw_query:1", "is_sink": True,
                                "category": "sql", "slot": "sql_value",
                                "arg_index": 0, "rationale": "x"}])
        return json.dumps([{"call_ref": "exec_one:2", "is_sink": False}])

    async def cb(s: ProgressSample):
        samples.append(s)

    soft, gaps = await discover_sinks_llm(calls, client, progress_cb=cb)
    # 至少 1 个命中 tick(detail 非 None)
    assert any(s.detail for s in samples)
    # 末尾是 finalize 汇总
    assert samples[-1].final is True
    assert "soft sinks" in (samples[-1].detail or "")
    # done = 去重后的函数数
    assert samples[-1].done == len({sc.block.id for sc in calls})
    # 软 sink 真产出
    assert len(soft) == 1
    assert soft[0].callee_name == "raw_query"


async def test_discover_sinks_llm_progress_cb_none_ok():
    """T4: progress_cb=None 时全程 no-op,功能不回归(返回空 sink)。"""
    calls = [_suspicious(line=1, callee="raw_query")]

    async def client(prompt, **kw):
        return "[]"  # LLM 判无 sink

    soft, gaps = await discover_sinks_llm(calls, client, progress_cb=None)
    assert soft == []
