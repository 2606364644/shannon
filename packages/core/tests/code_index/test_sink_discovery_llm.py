"""sink_discovery_llm 单测 — 半 sink 收集 + LLM 补召回(spec 方案 A)."""
import json

import pytest

from supernova_core.code_index.models import FuncBlock
from supernova_core.code_index.parameter_models import SinkCategory, SlotContext
from supernova_core.code_index.sink_discovery_llm import (
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


def test_jpa_createnativequery_not_in_candidates():
    """Java em.createNativeQuery(...) -- 规则 java-jpa-createnativequery 的 receiver_pattern
    已由 null 升级为 .+ 全覆盖（4d66a6ee），命中硬规则 sink，不再进 LLM 补召回候选（规则升级正向结果）。"""
    block = _block(language="java")
    parser = _FakeParser([("createNativeQuery", "em", ["uid"], 1)])
    out = collect_suspicious_calls([block], parser, source_provider=lambda b: b"src")
    assert out == []


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


def _suspicious(callee="raw_query", receiver="custom_db", line=1, arg="uid",
                file="app.py", name="handler", source="def handler(): pass"):
    from supernova_core.code_index.models import FuncBlock
    block = FuncBlock(
        id=f"{file}:{name}:{line}", file_path=file, function_name=name,
        start_line=line, end_line=line + 10, source_code=source,
        parameters=["uid"], language="python",
    )
    return SuspiciousCall(block=block, callee=callee, receiver=receiver,
                          arg_exprs=[arg], file_path=file, line=line, column=0)


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
    from supernova_core.code_index.parameter_models import IntraResult
    from supernova_core.code_index import chain_propagator  # 仅验类型可达, 实际用 mock

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
    from supernova_core.code_index.llm_taint_analyzer import _deterministic_intra_fallback
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
    from supernova_core.code_index.parameter_models import (
        ParameterPropagationGraph,
        ParameterSource,
        TaintFlow,
    )
    from supernova_core.code_index.chain_verdict import (
        _INJECTION_SLOTS,
        extract_candidate_chains,
    )
    from supernova_core.code_index.vuln_chain_builders.injection_builder import (
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
    """并发(治本2) + 文件级: 不同文件 chunk, 一个挂死超时被跳过, 另一个成功产 soft sink。

    文件级后诊断单位是文件 chunk: a.py 正常产 sink, b.py 挂死 → b.py chunk 被
    per_call_timeout 砍掉; a.py 的 soft sink 必须保留(不被并发的失败 chunk 带垮)。
    """
    import asyncio
    calls = [
        _suspicious(file="a.py", name="f", line=1, callee="raw_query"),
        _suspicious(file="b.py", name="g", line=1, callee="exec_one"),
    ]

    async def client(prompt, **kw):
        if "a.py" in prompt:  # a.py chunk 正常
            return json.dumps([{"call_ref": "raw_query:1", "is_sink": True,
                                "category": "sql", "slot": "sql_value",
                                "arg_index": 0, "rationale": "x"}])
        await asyncio.sleep(10)  # b.py chunk 挂死

    soft, _ = await discover_sinks_llm(
        calls, client, concurrency=2, per_call_timeout=0.2, max_calls=1)
    assert len(soft) == 1
    assert soft[0].callee_name == "raw_query"


async def test_discover_sinks_llm_reports_progress_and_hits():
    """progress_cb: 每个 chunk(文件)一次 tick(命中带 detail) + 末尾 finalize 汇总。

    文件级后 total/done = chunk 数(= 文件数, spec §3.1)。两个不同文件 → 2 chunk 2 tick。
    """
    from supernova_core.code_index.progress import ProgressSample

    # 两个不同文件 → 2 chunk; a.py 判 sink, b.py 判非 sink。
    calls = [
        _suspicious(file="a.py", name="f", line=1, callee="raw_query"),
        _suspicious(file="b.py", name="g", line=2, callee="other_q"),
    ]

    async def client(prompt, **kw):
        if "a.py" in prompt:
            return json.dumps([{"call_ref": "raw_query:1", "is_sink": True,
                                "category": "sql", "slot": "sql_value",
                                "arg_index": 0, "rationale": "x"}])
        return json.dumps([{"call_ref": "other_q:2", "is_sink": False}])

    samples: list[ProgressSample] = []

    async def cb(s: ProgressSample):
        samples.append(s)

    soft, _ = await discover_sinks_llm(calls, client, progress_cb=cb, max_calls=1)
    assert len(soft) == 1  # 只有 a.py 判 sink

    # 至少有一条 tick 带 hit detail(命中行) —— detail 非 None 标识命中。
    hit_ticks = [s for s in samples if not s.final and s.detail]
    assert hit_ticks, f"no hit-detail tick emitted: {samples}"
    assert "raw_query" in hit_ticks[0].detail  # detail 提到命中的 callee

    # 最后一条是 finalize 汇总, final=True, done == chunk 数(文件数 = 2)。
    assert samples[-1].final is True
    assert samples[-1].done == 2


async def test_discover_sinks_llm_progress_cb_none_ok():
    """progress_cb=None 全程 no-op, 返回正常(空 verdict → 空 soft)。"""
    calls = [_suspicious(line=1)]

    async def client(prompt, **kw):
        return "[]"  # 无 verdict → 无 soft sink

    soft, gaps = await discover_sinks_llm(calls, client, progress_cb=None)
    assert soft == []


async def test_discover_sinks_llm_skip_emits_note_via_progress_cb():
    """文件级: 不同文件 chunk, 一个超时 → emitter.note 经 progress_cb 上报(走 dispatcher)。

    on_skip 注入: 超时 chunk 的 file_path 经 idx 映射进 note detail(文件级后诊断
    单位是文件 chunk, 取代撞 Rich Live footer 的裸 logger.warning)。
    """
    import asyncio
    from supernova_core.code_index.progress import ProgressSample

    # 两个不同文件 → 2 chunk; slow.py 挂死, fast.py 正常 → 部分失败触发 on_skip。
    calls = [
        _suspicious(file="slow.py", name="f", line=1, callee="raw_query"),
        _suspicious(file="fast.py", name="g", line=2, callee="other_q"),
    ]

    async def client(prompt, **kw):
        if "slow.py" in prompt:
            await asyncio.sleep(10)  # slow.py chunk 挂死 → 超时
        return json.dumps([{"call_ref": "other_q:2", "is_sink": False}])

    samples: list[ProgressSample] = []

    async def cb(s):
        samples.append(s)

    await discover_sinks_llm(
        calls, client, progress_cb=cb, concurrency=2, per_call_timeout=0.2, max_calls=1)

    notes = [s for s in samples if s.note]
    assert notes, f"超时应经 note 上报: {samples}"
    assert "timed out" in notes[0].note
    assert "slow.py" in notes[0].note  # file_path 经 idx 映射(文件级)


# ===== spec 2026-07-10: sink 补召回 per-function → 文件级聚合 + chunking =====


async def test_discover_file_level_groups_same_file_into_one_call():
    """同文件多函数多可疑 call → 1 次 LLM 调用(文件级聚合, spec §3.1 核心)。

    回归锚点: per-function 时这会是 2 次调用(每函数一次); 文件级后同文件合并成 1 次。
    """
    calls = [
        _suspicious(file="app.py", name="f", line=1, callee="a"),
        _suspicious(file="app.py", name="g", line=2, callee="b"),
    ]
    n_calls = 0

    async def client(prompt, **kw):
        nonlocal n_calls
        n_calls += 1
        return json.dumps([
            {"call_ref": "a:1", "is_sink": True, "category": "sql",
             "slot": "sql_value", "arg_index": 0, "rationale": "x"},
            {"call_ref": "b:2", "is_sink": True, "category": "command",
             "slot": "cmd_argument", "arg_index": 0, "rationale": "y"},
        ])

    soft, _ = await discover_sinks_llm(calls, client)
    assert n_calls == 1, f"同文件应 1 次调用(文件级), 实际 {n_calls}"
    assert len(soft) == 2
    assert sorted(s.callee_name for s in soft) == ["a", "b"]


async def test_discover_file_level_prompt_lists_all_functions_and_calls():
    """文件级 prompt 含该文件所有可疑函数源码 + 全文件可疑 call 列表(spec §3.1)。"""
    calls = [
        _suspicious(file="app.py", name="get_user", line=1, callee="raw_query"),
        _suspicious(file="app.py", name="delete_user", line=2, callee="exec_cmd"),
    ]
    seen: list[str] = []

    async def client(prompt, **kw):
        seen.append(prompt)
        return "[]"

    await discover_sinks_llm(calls, client)
    assert len(seen) == 1  # 同文件 1 次
    p = seen[0]
    assert "get_user" in p and "delete_user" in p   # 两个函数源码都在
    assert "raw_query:1" in p and "exec_cmd:2" in p  # 两个 call_ref 都在


async def test_discover_file_level_verdict_routes_multiple_sinks():
    """文件级一次调用返回多 verdict → 按 call_ref 归位多个软 sink(文件内 line 唯一)。"""
    calls = [
        _suspicious(file="svc.py", name="f", line=10, callee="sink_a"),
        _suspicious(file="svc.py", name="g", line=20, callee="sink_b"),
        _suspicious(file="svc.py", name="h", line=30, callee="safe_call"),
    ]

    async def client(prompt, **kw):
        return json.dumps([
            {"call_ref": "sink_a:10", "is_sink": True, "category": "sql",
             "slot": "sql_value", "arg_index": 0, "rationale": "a"},
            {"call_ref": "sink_b:20", "is_sink": True, "category": "ssrf",
             "slot": "url", "arg_index": 0, "rationale": "b"},
            {"call_ref": "safe_call:30", "is_sink": False},
        ])

    soft, _ = await discover_sinks_llm(calls, client)
    assert len(soft) == 2  # sink_a + sink_b; safe_call 被否决
    assert sorted(f"{s.callee_name}:{s.line}" for s in soft) == ["sink_a:10", "sink_b:20"]


async def test_discover_separate_files_are_separate_calls():
    """max_calls 上限(spec §3 模块2): max_calls=1 → 两文件(各 1 call)拆成 2 次调用。

    旧版「绝不跨文件」时默认就分离; 跨文件合并后默认会合并, 故此处显式 max_calls=1
    验证 call 数上限仍能强制分离(失败粒度可控)。
    """
    calls = [
        _suspicious(file="a.py", name="f", line=1, callee="x"),
        _suspicious(file="b.py", name="g", line=1, callee="y"),
    ]
    n_calls = 0

    async def client(prompt, **kw):
        nonlocal n_calls
        n_calls += 1
        return "[]"

    await discover_sinks_llm(calls, client, max_calls=1)
    assert n_calls == 2, f"max_calls=1 应拆 2 次, 实际 {n_calls}"


async def test_discover_cross_file_merges_small_files_into_one_call():
    """跨文件贪心合并(spec §3 模块1): 两个小文件, 默认 max_calls(=100) → 合并成 1 次调用。

    prompt 的 {file_paths} 含两文件 join(如 "a.py, b.py"), 而非只一个文件。
    这是本 spec 的核心收益(kol 259 文件 → ~7 chunk)。
    """
    calls = [
        _suspicious(file="a.py", name="f", line=1, callee="x"),
        _suspicious(file="b.py", name="g", line=1, callee="y"),
    ]
    seen: list[str] = []

    async def client(prompt, **kw):
        seen.append(prompt)
        return "[]"

    await discover_sinks_llm(calls, client)
    assert len(seen) == 1, f"跨文件应合并成 1 次调用, 实际 {len(seen)}"
    assert "a.py, b.py" in seen[0]  # {file_paths} join 多文件(spec §3 模块3)


async def test_discover_per_call_timeout_defaults_to_120(monkeypatch):
    """文件级 prompt 更重 → discover_sinks_llm 不传 per_call_timeout 时默认 120s
    (局部覆盖, 不动 concurrency 全局 60s; spec §3.2)。显式传值优先。"""
    from supernova_core.code_index import sink_discovery_llm as mod

    captured: list = []

    async def fake_map(items, fn, *, concurrency, per_call_timeout, label, on_skip):
        captured.append(per_call_timeout)
        return []

    monkeypatch.setattr(mod, "map_llm_with_bounds", fake_map)

    async def dummy_client(prompt, **kw):
        return "[]"

    await discover_sinks_llm([_suspicious()], dummy_client)  # 不传 → 默认 120
    assert captured[-1] == 120.0, f"默认应 120s, 实际 {captured[-1]}"

    await discover_sinks_llm([_suspicious()], dummy_client, per_call_timeout=5)
    assert captured[-1] == 5, f"显式传值应优先, 实际 {captured[-1]}"


async def test_discover_large_file_chunks_into_multiple_calls():
    """大文件(源码 token 超 token_threshold)→ 按函数拆 chunk → 多次调用(防爆 context)。"""
    calls = [
        _suspicious(file="big.go", name="A", line=1, callee="ra",
                    source="x = 1\n" * 200),  # ~300 tokens
        _suspicious(file="big.go", name="B", line=2, callee="rb",
                    source="x = 1\n" * 200),
    ]
    n_calls = 0

    async def client(prompt, **kw):
        nonlocal n_calls
        n_calls += 1
        return "[]"

    await discover_sinks_llm(calls, client, token_threshold=100)
    assert n_calls == 2, f"大文件应按函数拆 2 chunk(2 次调用), 实际 {n_calls}"


async def test_discover_per_call_timeout_honors_env_override(monkeypatch):
    """spec §3.2: SUPERNOVA_LLM_PER_CALL_TIMEOUT env 须能覆盖 sink 的 per-call 上限。

    文件级默认 120s(下限, prompt 更重), 但运营设 env=200 给慢模型必须生效 ——
    不能被硬编码 120 绕过(旧版 effective_timeout 恒 120 → env 失效, 违反
    spec §3.2「均可经 env 覆盖」+ concurrency.py docstring)。回归锚点。
    """
    from supernova_core.code_index import sink_discovery_llm as mod

    monkeypatch.setenv("SUPERNOVA_LLM_PER_CALL_TIMEOUT", "200")

    captured: list = []

    async def fake_map(items, fn, *, concurrency, per_call_timeout, label, on_skip):
        captured.append(per_call_timeout)
        return []

    monkeypatch.setattr(mod, "map_llm_with_bounds", fake_map)

    async def dummy_client(prompt, **kw):
        return "[]"

    await discover_sinks_llm([_suspicious()], dummy_client)  # 不传 per_call_timeout
    assert captured[-1] == 200.0, f"env=200 应生效, 实际 {captured[-1]}"


async def test_discover_skips_malformed_verdict_field_keeps_other_sinks():
    """文件级回归锚点: 一条 verdict 字段 malformed(arg_index=null) 只跳过该 sink,
    不丢整文件 chunk。

    旧 per-function 只丢一个函数; 文件级聚合后若 _to_soft_sink 的 int() 无 per-item
    防护, null 字段会崩 _discover_one → map 标整 chunk _Skip → 该文件所有 sink 丢
    (含已 valid 的), 影响面被放大。spec §3.1 verdict 归位须容错。
    """
    calls = [
        _suspicious(file="svc.py", name="f", line=10, callee="good_sink"),
        _suspicious(file="svc.py", name="g", line=20, callee="bad_sink"),
    ]

    async def client(prompt, **kw):
        # bad_sink 的 arg_index=null → int(None) 旧版崩; good_sink 正常
        return json.dumps([
            {"call_ref": "good_sink:10", "is_sink": True, "category": "sql",
             "slot": "sql_value", "arg_index": 0, "rationale": "g"},
            {"call_ref": "bad_sink:20", "is_sink": True, "category": "sql",
             "slot": "sql_value", "arg_index": None, "rationale": "b"},
        ])

    soft, _ = await discover_sinks_llm(calls, client)
    # good_sink 必须保留(bad_sink 的 malformed 字段只跳过它自己, 不带垮整 chunk)
    assert any(s.callee_name == "good_sink" for s in soft), \
        f"malformed arg_index 不应丢整 chunk, good_sink 应保留: {soft}"


async def test_discover_sinks_threshold_derives_from_model():
    """model='glm-5.2' -> token_threshold 派生 750K, 大函数进 1 chunk(spec §3 模块3)。

    glm-5.2 context 1M × 0.75 = 750K; big_src ~100K tokens < 750K -> 1 chunk(1 次 LLM 调用)。
    """
    import asyncio
    from supernova_core.code_index.models import FuncBlock

    big_src = "x = 1\n" * 100_000  # ~500K chars -> ~125K tokens(ascii)
    block = FuncBlock(
        id="app.py:f:1", file_path="app.py", function_name="f",
        start_line=1, end_line=5, source_code=big_src,
        parameters=["uid"], language="python",
    )
    sc = SuspiciousCall(block=block, callee="exec", receiver=None,
                        arg_exprs=["uid"], file_path="app.py", line=1, column=0)
    calls = []

    async def fake_client(prompt, **kwargs):
        calls.append(prompt)
        return "[]"  # 空 verdict

    sinks, gaps = await discover_sinks_llm([sc], fake_client, model="glm-5.2")
    assert len(calls) == 1  # 整个大函数进 1 chunk(750K 容得下 125K)
    assert sinks == [] and gaps == []


async def test_discover_sinks_threshold_default_model(monkeypatch):
    """model=None -> 走默认 128K context -> threshold 96K。

    big_src ~125K tokens > 96K, 但单 block 超 threshold 独立成 1 chunk(无法再拆)。
    """
    from supernova_core.code_index.models import FuncBlock

    big_src = "x = 1\n" * 100_000  # ~125K tokens
    block = FuncBlock(
        id="app.py:f:1", file_path="app.py", function_name="f",
        start_line=1, end_line=5, source_code=big_src,
        parameters=["uid"], language="python",
    )
    sc = SuspiciousCall(block=block, callee="exec", receiver=None,
                        arg_exprs=["uid"], file_path="app.py", line=1, column=0)
    calls = []

    async def fake_client(prompt, **kwargs):
        calls.append(prompt)
        return "[]"

    # model=None -> 96K threshold; 125K tokens > 96K 但单 block 独立成 1 chunk
    sinks, gaps = await discover_sinks_llm([sc], fake_client, model=None)
    assert len(calls) == 1  # 单 block 超阈值独立成 chunk



# ===== §3(deepsec 吸收): candidate schema 扩展 context/arg/exclude patterns =====

class TestDeepsecCandidateSchemaNarrowing:
    """deepsec §3 候选 schema 扩展:context_patterns/arg_patterns/exclude_patterns 收窄。

    用真实 TypeScriptParser/PythonParser 解析,验证收窄字段真的生效(省 LLM 调用)。
    """

    def _ts_suspicious(self, src):
        import tempfile, pathlib
        from supernova_core.code_index.parsers.typescript_parser import TypeScriptParser
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "a.ts"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        src_bytes = src.encode("utf-8")
        return collect_suspicious_calls(blocks, parser,
                                        source_provider=lambda b: src_bytes)

    def _py_suspicious(self, src):
        import tempfile, pathlib
        from supernova_core.code_index.parsers.python_parser import PythonParser
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "a.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        src_bytes = src.encode("utf-8")
        return collect_suspicious_calls(blocks, parser,
                                        source_provider=lambda b: src_bytes)

    def test_fs_readfile_with_req_reference_is_candidate(self):
        """fs.readFile 拼了 req.body.path(arg 含 req 引用)→ §3.3 arg_patterns 收窄命中 → 候选。"""
        src = (
            "import * as fs from 'fs';\n"
            "function f(req){ fs.readFile(req.body.path, cb); }\n"
        )
        out = self._ts_suspicious(src)
        assert any(c.callee == "readFile" for c in out), \
            "fs.readFile(req.body.path) 应进候选(arg 含 req 引用)"

    def test_fs_readfile_literal_path_not_candidate(self):
        """fs.readFile('/etc/passwd')(纯字面量路径,arg 不含拼接/req)→ §3.3 arg_patterns 收窄排除。"""
        src = (
            "import * as fs from 'fs';\n"
            "function f(){ fs.readFile('/etc/passwd', cb); }\n"
        )
        out = self._ts_suspicious(src)
        assert not any(c.callee == "readFile" for c in out), \
            "fs.readFile(纯字面量) 不应进候选(arg_patterns 收窄排除)"

    def test_object_assign_with_req_body_is_candidate(self):
        """Object.assign({}, req.body)(context 含 req./body)→ §3.4 原型污染候选。"""
        src = (
            "function f(req){ Object.assign({}, req.body); }\n"
        )
        out = self._ts_suspicious(src)
        assert any(c.callee == "assign" and c.receiver == "Object" for c in out), \
            "Object.assign({}, req.body) 应进候选(context 含 req 引用)"

    def test_object_assign_static_not_candidate(self):
        """Object.assign({}, defaults)(无 req 引用)→ §3.4 context_patterns 排除。"""
        src = "function f(){ Object.assign({}, defaults); }\n"
        out = self._ts_suspicious(src)
        assert not any(c.callee == "assign" for c in out), \
            "Object.assign({}, defaults) 不应进候选(context 无 req 引用)"

    def test_json_parse_with_req_is_candidate(self):
        """JSON.parse(req.body)(context 含 req.)→ §3.7 候选(供 LLM 判二次注入)。"""
        src = "function f(req){ const d = JSON.parse(req.body); }\n"
        out = self._ts_suspicious(src)
        assert any(c.callee == "parse" and c.receiver == "JSON" for c in out)

    def test_json_parse_static_not_candidate(self):
        """JSON.parse('{\"a\":1}')(无 req 引用)→ §3.7 context_patterns 排除。"""
        src = 'function f(){ const d = JSON.parse(\'{"a":1}\'); }\n'
        out = self._ts_suspicious(src)
        assert not any(c.callee == "parse" for c in out)

    def test_yaml_load_is_candidate(self):
        """yaml.load(blob)(JS 反序列化,类比 Python py-yaml-load)→ §3.6 候选。"""
        src = "import * as yaml from 'yaml';\nfunction f(blob){ yaml.load(blob); }\n"
        out = self._ts_suspicious(src)
        assert any(c.callee == "load" and c.receiver == "yaml" for c in out)

    def test_python_json_loads_with_request_is_candidate(self):
        """json.loads(request.body)(context 含 request.)→ §3.7 Python 候选。"""
        src = (
            "import json\n"
            "def f(request):\n"
            "    data = json.loads(request.body)\n"
        )
        out = self._py_suspicious(src)
        assert any(c.callee == "loads" and c.receiver == "json" for c in out)

    def test_python_json_loads_static_not_candidate(self):
        """json.loads('{}')(无 request 引用)→ §3.7 Python context_patterns 排除。"""
        src = (
            "import json\n"
            "def f():\n"
            "    data = json.loads('{}')\n"
        )
        out = self._py_suspicious(src)
        assert not any(c.callee == "loads" for c in out)


# ===== spec 2026-08-27 §5：discovery 多轮 agent 路径 =====

class _AgentResult:
    def __init__(self, *, success=True, structured_output=None, text="", error=None):
        self.success = success
        self.structured_output = structured_output
        self.text = text
        self.error = error


async def test_discover_sinks_llm_agent_path(monkeypatch):
    """多轮 agent 路径：prompt 只给 call 清单（无源码快照，agent 自己 read）、
    output_format/_SINK_VERDICT_SCHEMA 透传、agent_name=gn-discovery-sink-NNN、
    structured_output（list）→ 解析软 sink（产物形态与单次路径一致）。"""
    from supernova_core.code_index.sink_discovery_llm import _SINK_VERDICT_SCHEMA
    calls_rec = []

    async def fake_agent(prompt, *, output_format=None, agent_name=None):
        calls_rec.append({"prompt": prompt, "schema": output_format,
                          "name": agent_name})
        return _AgentResult(structured_output=[
            {"call_ref": "raw_query:1", "is_sink": True, "category": "sql",
             "slot": "sql_value", "arg_index": 0, "rationale": "raw SQL"}])

    soft, gaps = await discover_sinks_llm([_suspicious()], None,
                                          discovery_agent=fake_agent)
    assert len(soft) == 1
    assert soft[0].rule_id == "llm-discovered"
    assert soft[0].needs_review is True
    assert len(gaps) == 1
    assert len(calls_rec) == 1
    assert calls_rec[0]["schema"] is _SINK_VERDICT_SCHEMA
    assert calls_rec[0]["name"] == "gn-discovery-sink-001"
    # agent 版 prompt：call 清单在，源码快照不在（瘦身——agent 自己 read）
    assert "raw_query" in calls_rec[0]["prompt"]
    assert "def handler(): pass" not in calls_rec[0]["prompt"]


async def test_discover_sinks_llm_agent_failure_chunk_degrades():
    """agent success=False → 该 chunk 降级空返回（对齐单次路径 raise/超时降级）。"""
    async def failing_agent(prompt, *, output_format=None, agent_name=None):
        return _AgentResult(success=False, error="timeout")

    soft, gaps = await discover_sinks_llm([_suspicious()], None,
                                          discovery_agent=failing_agent)
    assert soft == [] and gaps == []


async def test_discover_sinks_llm_agent_timeout_floor(monkeypatch):
    """agent 路径 per_call_timeout 地板 = SUPERNOVA_GN_DISCOVERY_AGENT_TIMEOUT
    （默认 300s——多轮 agent 120s 单次档不够；spec 2026-08-27 §5）。"""
    from supernova_core.code_index import sink_discovery_llm as mod
    captured = {}

    async def fake_map(items, fn, *, concurrency, per_call_timeout=None,
                       label="", on_skip=None):
        captured["timeout"] = per_call_timeout
        return []

    monkeypatch.setattr(mod, "map_llm_with_bounds", fake_map)

    async def fake_agent(prompt, *, output_format=None, agent_name=None):
        return _AgentResult(text="[]")

    await discover_sinks_llm([_suspicious()], None, discovery_agent=fake_agent)
    assert captured["timeout"] >= 300
