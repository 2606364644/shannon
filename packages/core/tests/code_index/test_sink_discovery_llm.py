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


def test_soft_sink_does_not_break_injection_whitelist():
    """软 sink 的 rule_id/sink_subtype 不接触 injection 白名单维度(spec §6).

    现实校验(Step 1 grep): injection 白名单 = `VALID_INJECTION_CATEGORIES`
    (finding_models.py:28), 其成员是 issue_type('sql_injection' 等), 由
    `parse_and_validate_findings` 按 `issue_type` 校验 —— 既非 brief 假设的
    vuln-class `category`, 亦非 `sink_subtype`. 更关键: 软 sink 走
    injection_builder → InjectionVulnerability(queue_schemas), 该路径根本不调用
    parse_and_validate_findings, 故白名单永远不会拒绝软 sink.

    本测试锁定此不变量: 软 sink 的 rule_id/subtype 不在任何 agent 白名单内,
    且软 sink 产出物(InjectionVulnerability)不经白名单校验路径."""
    from shannon_core.code_index.finding_models import (
        VALID_INJECTION_CATEGORIES,
        AGENT_TYPE_WHITELIST,
    )
    # 软 sink 的 SinkCategory(SQL)是 sink 级分类, 与白名单 issue_type 不混字段:
    sc = _suspicious(arg="uid")
    import asyncio
    async def client(prompt, **kw):
        return json.dumps([{"call_ref": "raw_query:1", "is_sink": True,
                            "category": "sql", "slot": "sql_value", "arg_index": 0,
                            "rationale": "x"}])
    soft, _ = asyncio.run(discover_sinks_llm([sc], client))
    assert soft[0].rule_id == "llm-discovered"
    assert soft[0].category.value == "sql"  # SinkCategory(sink 级)
    # 白名单维度(issue_type)不接触软 sink 的 rule_id / sink_subtype:
    assert soft[0].sink_subtype not in VALID_INJECTION_CATEGORIES
    assert not any(soft[0].sink_subtype in v for v in AGENT_TYPE_WHITELIST.values())
    # injection_builder 产出 InjectionVulnerability(非 VulnFinding), 不调白名单校验:
    from shannon_core.code_index.vuln_chain_builders.injection_builder import (
        build_injection_findings,
    )
    import inspect
    src = inspect.getsource(build_injection_findings)
    assert "parse_and_validate_findings" not in src  # builder 不走白名单路径
    assert "VALID_INJECTION_CATEGORIES" not in src   # 不引用白名单
