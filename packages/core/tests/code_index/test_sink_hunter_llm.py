import json

import pytest

from supernova_core.code_index.models import FuncBlock
from supernova_core.code_index.parameter_models import SinkCategory, SlotContext
from supernova_core.code_index.sink_discovery_llm import (
    SinkHunterCandidate,
    collect_entry_handler_blocks,
    discover_sinks_by_entry,
)


def _block(*, id="b1", file_path="Ctl.java", function_name="handler",
           start_line=1, source="src", language="java"):
    return FuncBlock(
        id=id, file_path=file_path, function_name=function_name,
        start_line=start_line,
        end_line=start_line + source.count("\n") + 1,
        source_code=source, language=language,
        parameters=[],
    )


def test_collect_entry_handler_blocks_keeps_sinkless_entries():
    b_entry = _block(id="e1", function_name="apiModify")
    b_with_sink = _block(id="s1", function_name="hasSink")
    b_other = _block(id="o1", function_name="helper")
    out = collect_entry_handler_blocks(
        [b_entry, b_with_sink, b_other],
        entry_point_ids={"e1", "s1"},
        sink_func_ids={"s1"},
    )
    assert [c.block.id for c in out] == ["e1"]
    assert isinstance(out[0], SinkHunterCandidate)


def test_collect_entry_handler_blocks_empty_when_all_have_sinks():
    b = _block(id="e1")
    out = collect_entry_handler_blocks([b], entry_point_ids={"e1"}, sink_func_ids={"e1"})
    assert out == []


@pytest.mark.asyncio
async def test_discover_sinks_by_entry_finds_fastjson_parseobject():
    # 对称原始版 INJ-01: ClusterConfigController.apiModifyClusterConfig
    #   @RequestBody String payload -> JSON.parseObject(payload)  (fastjson autotype, RCE)
    src = '''  @PostMapping("/cluster/config/modify_single")
  public String apiModifyClusterConfig(@RequestBody String payload) {
    JSONObject o = JSON.parseObject(payload);
    return "ok";
  }'''
    block = _block(id="e1", function_name="apiModifyClusterConfig", source=src)
    cands = [SinkHunterCandidate(block=block)]

    async def client(prompt, **kw):
        return json.dumps([{
            "sink": "JSON.parseObject(payload)",
            "category": "deserialization",
            "dangerous_arg": "payload",
            "line": 3,
            "is_sink": True,
            "rationale": "fastjson autotype deserialization of user-controlled body",
        }])

    soft, gaps = await discover_sinks_by_entry(cands, client)
    assert len(soft) == 1
    s = soft[0]
    assert s.rule_id == "llm-discovered-sink"
    assert s.needs_review is True
    assert s.category == SinkCategory.DESERIALIZATION
    assert s.file_path == "Ctl.java"
    assert s.caller_id == "e1"


@pytest.mark.asyncio
async def test_discover_sinks_by_entry_none_client_degrades():
    soft, gaps = await discover_sinks_by_entry([], None)
    assert soft == [] and gaps == []


@pytest.mark.asyncio
async def test_discover_sinks_by_entry_drops_is_sink_false():
    block = _block(id="e1", function_name="h", source="void h(){}")
    async def client(prompt, **kw):
        return json.dumps([{"sink": "foo()", "category": "sql", "line": 1,
                            "is_sink": False, "rationale": "safe"}])
    soft, _ = await discover_sinks_by_entry([SinkHunterCandidate(block=block)], client)
    assert soft == []


@pytest.mark.asyncio
async def test_hunter_sink_routes_to_injection_queue():
    """C1 回归测试(对称 test_soft_sink_does_not_break_injection_whitelist):

    hunter prompt 旧版未索取 slot → _to_hunter_sink 恒 SlotContext.GENERIC →
    extract_candidate_chains 因 generic∉_INJECTION_SLOTS 滤掉 → hunter sinks 到不了
    injection queue(击中 plan 核心承诺)。

    本测试锁:
      1. discover_sinks_by_entry 产 hunter soft sink(rule_id=llm-discovered-sink);
      2. LLM 漏 slot 字段时, category=deserialization 须回退到 slot=DESERIALIZE_OBJ
         (而非 GENERIC);
      3. 构造 TaintFlow{sink_call_site_id=soft_sink.id, sink_slot=DESERIALIZE_OBJ};
      4. extract_candidate_chains 必须抽出该链(不被路由滤掉 —— deserialize∈_INJECTION_SLOTS)。

    RED(before fix): slot=GENERIC → _to_hunter_sink 出 GENERIC → 第 2 步断言失败;
                     即便侥幸过了, 第 4 步 extract 0 chains(被路由滤)。
    GREEN(after fix A+B): slot 经 category 回退 = DESERIALIZE_OBJ → 路由放进 injection 桶。
    """
    from supernova_core.code_index.parameter_models import (
        ParameterPropagationGraph,
        ParameterSource,
        TaintFlow,
    )
    from supernova_core.code_index.chain_verdict import (
        _INJECTION_SLOTS,
        extract_candidate_chains,
    )

    # 1) hunter 产 soft sink —— LLM 漏 slot 字段(证 fallback B 工作)。
    #    category=deserialization(fastjson JSON.parseObject, plan NodeGoat INJ-01 场景)。
    src = '''  @PostMapping("/cluster/config/modify_single")
  public String apiModifyClusterConfig(@RequestBody String payload) {
    JSONObject o = JSON.parseObject(payload);
    return "ok";
  }'''
    block = _block(id="e1", function_name="apiModifyClusterConfig", source=src)
    cands = [SinkHunterCandidate(block=block)]

    async def client(prompt, **kw):
        # 故意不给 slot —— 走 _to_hunter_sink 的 category→slot 回退(B)。
        return json.dumps([{
            "sink": "JSON.parseObject(payload)",
            "category": "deserialization",
            "dangerous_arg": "payload",
            "line": 3,
            "is_sink": True,
            "rationale": "fastjson autotype RCE",
        }])

    soft, _ = await discover_sinks_by_entry(cands, client)
    assert len(soft) == 1
    soft_sink = soft[0]
    assert soft_sink.rule_id == "llm-discovered-sink"
    # 关键断言: slot 须是 DESERIALIZE_OBJ(经 category 回退), 不能是 GENERIC。
    assert soft_sink.dangerous_slots[0].slot == SlotContext.DESERIALIZE_OBJ, (
        f"slot={soft_sink.dangerous_slots[0].slot!r}, expected DESERIALIZE_OBJ "
        f"(category=deserialization 须回退到 deserialize 过 _INJECTION_SLOTS 路由)"
    )
    assert "deserialize" in _INJECTION_SLOTS  # 路由前置: deserialize 归 injection

    # 2) 构造最小 pgraph: TaintFlow 终点指向该 hunter sink。
    pgraph = ParameterPropagationGraph(
        taint_flows=[TaintFlow(
            flow_id=f"ep#1->{soft_sink.id}",
            entry_point_id="ep#1",
            source_param="payload",
            source_type=ParameterSource.BODY_FIELD,
            sink_call_site_id=soft_sink.id,
            sink_slot=SlotContext.DESERIALIZE_OBJ,
        )],
        language_coverage=["java"],
    )

    # 3) 路由层: hunter sink 链必须被 extract_candidate_chains 抽出(deserialize∈
    #    _INJECTION_SLOTS)。修复前: GENERIC → 0 chains; 修复后: 1 chain。
    chains = extract_candidate_chains(pgraph, vuln_class="injection")
    assert len(chains) == 1, f"hunter sink chain dropped at routing: {chains}"
    assert chains[0].sink_call_site_id == soft_sink.id
    assert chains[0].sink_slot == "deserialize"
