import pytest
from shannon_core.code_index.models import FuncBlock
from shannon_core.code_index.sink_discovery_llm import (
    SinkHunterCandidate, collect_entry_handler_blocks,
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
