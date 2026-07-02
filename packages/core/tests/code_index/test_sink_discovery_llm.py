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
    # system 在候选表(通用 command 列)但规则库只覆盖 os/subprocess receiver → 收集
    block = _block()
    parser = _FakeParser([("system", None, ["uid"], 1)])
    out = collect_suspicious_calls([block], parser, source_provider=lambda b: b"src")
    assert len(out) == 1
    assert out[0].callee == "system"
    assert out[0].receiver is None


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


def test_wide_words_no_longer_trigger():
    """宽词 format/open/where/fetch 已从候选表删除 —— 裸调用不再误触发送 LLM(防回退)。"""
    for callee in ("format", "open", "where", "fetch"):
        block = _block()
        parser = _FakeParser([(callee, None, ["uid"], 1)])
        out = collect_suspicious_calls([block], parser, source_provider=lambda b: b"src")
        assert out == [], f"{callee} 不应再触发补召回(宽词已删)"


def test_go_capitalized_raw_collected():
    """Go tx.Raw(...) —— 大写 Raw 命中候选(case-sensitive go 列);规则 receiver 未覆盖 tx → 收集。"""
    block = _block(language="go")
    parser = _FakeParser([("Raw", "tx", ["uid"], 1)])
    out = collect_suspicious_calls([block], parser, source_provider=lambda b: b"src")
    assert len(out) == 1
    assert out[0].callee == "Raw"


def test_jpa_createnativequery_collected():
    """Java em.createNativeQuery(...) —— 命中候选;规则只覆盖 bare → 收集(补召回)。"""
    block = _block(language="java")
    parser = _FakeParser([("createNativeQuery", "em", ["uid"], 1)])
    out = collect_suspicious_calls([block], parser, source_provider=lambda b: b"src")
    assert len(out) == 1
    assert out[0].callee == "createNativeQuery"


def test_receiver_constraint_filters():
    """TS raw —— 候选 receivers_any 集合命中才收集;集合外 receiver 不收集(收窄生效)。"""
    block = _block(language="typescript")
    hit = _FakeParser([("raw", "mongoose", ["uid"], 1)])      # mongoose ∈ 候选 receivers
    miss = _FakeParser([("raw", "somethingElse", ["uid"], 1)])  # ∉ 候选 receivers
    assert len(collect_suspicious_calls([block], hit, source_provider=lambda b: b"src")) == 1
    assert collect_suspicious_calls([block], miss, source_provider=lambda b: b"src") == []


def test_case_sensitivity_by_language():
    """go/java 大小写敏感;python 等其余不敏感。"""
    # go: Raw 命中(大写),raw 不命中(小写,case-sensitive)
    go = _block(language="go")
    assert len(collect_suspicious_calls(
        [go], _FakeParser([("Raw", "tx", ["uid"], 1)]), source_provider=lambda b: b"src")) == 1
    assert collect_suspicious_calls(
        [go], _FakeParser([("raw", "tx", ["uid"], 1)]), source_provider=lambda b: b"src") == []
    # python: Text 与 text 都命中(不敏感);session ∈ 候选 receivers,规则只覆盖 bare → 收集
    py = _block()
    assert len(collect_suspicious_calls(
        [py], _FakeParser([("Text", "session", ["uid"], 1)]), source_provider=lambda b: b"src")) == 1
    assert len(collect_suspicious_calls(
        [py], _FakeParser([("text", "session", ["uid"], 1)]), source_provider=lambda b: b"src")) == 1


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
    """progress_cb: 每个 function 一次 tick(命中带 detail) + 末尾 finalize 汇总。"""
    from shannon_core.code_index.progress import ProgressSample

    # 两个不同 block(line=1/2 → block.id 不同), 第一个判 sink, 第二个判非 sink。
    calls = [_suspicious(line=1), _suspicious(line=2)]

    async def client(prompt, **kw):
        if "raw_query:1" in prompt:
            return json.dumps([{"call_ref": "raw_query:1", "is_sink": True,
                                "category": "sql", "slot": "sql_value",
                                "arg_index": 0, "rationale": "x"}])
        return json.dumps([{"call_ref": "raw_query:2", "is_sink": False}])

    samples: list[ProgressSample] = []

    async def cb(s: ProgressSample):
        samples.append(s)

    soft, _ = await discover_sinks_llm(calls, client, progress_cb=cb)
    assert len(soft) == 1  # 只有第一个判 sink

    # 至少有一条 tick 带 hit detail(命中行) —— detail 非 None 标识命中。
    hit_ticks = [s for s in samples if not s.final and s.detail]
    assert hit_ticks, f"no hit-detail tick emitted: {samples}"
    assert "raw_query" in hit_ticks[0].detail  # detail 提到命中的 callee

    # 最后一条是 finalize 汇总, final=True, done == 唯一 function 数(2 个 block)。
    assert samples[-1].final is True
    assert samples[-1].done == len({sc.block.id for sc in calls})


async def test_discover_sinks_llm_progress_cb_none_ok():
    """progress_cb=None 全程 no-op, 返回正常(空 verdict → 空 soft)。"""
    calls = [_suspicious(line=1)]

    async def client(prompt, **kw):
        return "[]"  # 无 verdict → 无 soft sink

    soft, gaps = await discover_sinks_llm(calls, client, progress_cb=None)
    assert soft == []


async def test_discover_sinks_llm_skip_emits_note_via_progress_cb():
    """per-function 超时 → emitter.note 经 progress_cb 上报(走 dispatcher, 非裸 warning)。

    on_skip 注入: 超时函数名经 idx 映射进 note detail, 取代撞 Rich Live footer 的
    裸 logger.warning(redirect_stderr=False 硬约束)。
    """
    import asyncio
    from shannon_core.code_index.progress import ProgressSample

    calls = [_suspicious(line=1), _suspicious(line=2)]  # 2 个不同 block.id

    async def client(prompt, **kw):
        if "raw_query:1" in prompt:
            await asyncio.sleep(10)  # 第一个函数挂死 → 超时
        return json.dumps([{"call_ref": "raw_query:2", "is_sink": False}])

    samples: list[ProgressSample] = []

    async def cb(s):
        samples.append(s)

    await discover_sinks_llm(
        calls, client, progress_cb=cb, concurrency=2, per_call_timeout=0.2)

    notes = [s for s in samples if s.note]
    assert notes, f"超时应经 note 上报: {samples}"
    assert "timed out" in notes[0].note
    assert "handler" in notes[0].note  # block.function_name 经 idx 映射
