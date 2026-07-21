import json

import pytest

from shannon_core.code_index.models import FuncBlock
from shannon_core.code_index.parameter_models import SinkCategory, SlotContext
from shannon_core.code_index.sink_discovery_llm import (
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
