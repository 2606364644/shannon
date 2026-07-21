from unittest.mock import AsyncMock, MagicMock

import pytest
from openai.types.responses import ResponseTextDeltaEvent

from supernova_core.agents.openai_stream_collector import StreamCollector, _item_tool_args


def _text_event(delta: str):
    ev = MagicMock()
    ev.type = "raw_response_event"
    # openai 2.x: ResponseTextDeltaEvent 还要求 logprobs + sequence_number，
    # 构造真实实例以保留实现侧 isinstance(data, ResponseTextDeltaEvent) 精确判定。
    ev.data = ResponseTextDeltaEvent(
        type="response.output_text.delta",
        delta=delta,
        item_id="i",
        output_index=0,
        content_index=0,
        logprobs=[],
        sequence_number=0,
    )
    return ev


def _run_item_event(item_type: str, name: str, output: str | None = None):
    item = MagicMock()
    item.type = item_type
    item.output = output
    item.raw_item = MagicMock()
    ev = MagicMock()
    ev.type = "run_item_stream_event"
    ev.name = name
    ev.item = item
    return ev


@pytest.mark.asyncio
async def test_message_output_counts_turn():
    """每个 message_output_item（模型响应）= 1 turn，文本随该 turn 上报"""
    audit = AsyncMock()
    collector = StreamCollector(audit)
    await collector.on_event(_text_event("hello "))
    await collector.on_event(_text_event("world"))
    await collector.on_event(_run_item_event("message_output_item", "message_output_created"))
    await collector.on_event(_text_event("second"))
    await collector.on_event(_run_item_event("message_output_item", "message_output_created"))
    await collector.close()
    assert collector.text == "hello worldsecond"
    assert collector.turns == 2
    audit.log_assistant_turn.assert_any_call(1, "hello world")
    audit.log_assistant_turn.assert_any_call(2, "second")


@pytest.mark.asyncio
async def test_tool_call_counts_turn():
    """tool_call_item 也是模型响应，计 1 turn"""
    audit = AsyncMock()
    collector = StreamCollector(audit)
    await collector.on_event(_run_item_event("tool_call_item", "tool_called"))
    await collector.on_event(_run_item_event("tool_call_output_item", "tool_output", output="result-data"))
    assert collector.tool_call_count == 1
    assert collector.turns == 1
    audit.log_tool_start.assert_awaited()
    audit.log_tool_end.assert_awaited_with("result-data")


@pytest.mark.asyncio
async def test_tool_loop_counts_two_turns():
    """工具 loop：调工具(响应1) + 给答案(响应2) = 2 turns，答案上报为 turn 2"""
    audit = AsyncMock()
    collector = StreamCollector(audit)
    # 响应1：调工具（无文本）
    await collector.on_event(_run_item_event("tool_call_item", "tool_called"))
    await collector.on_event(_run_item_event("tool_call_output_item", "tool_output", output="hello"))
    # 响应2：给答案
    await collector.on_event(_text_event("the result is hello"))
    await collector.on_event(_run_item_event("message_output_item", "message_output_created"))
    await collector.close()
    assert collector.turns == 2
    audit.log_assistant_turn.assert_any_call(2, "the result is hello")


@pytest.mark.asyncio
async def test_close_flushes_trailing_text():
    """流结束时若有未随 run item flush 的尾文本，作为最后 turn 上报（兜底 turn 1）"""
    audit = AsyncMock()
    collector = StreamCollector(audit)
    await collector.on_event(_text_event("only text, no item"))
    await collector.close()
    assert collector.turns == 0  # 无 run item
    audit.log_assistant_turn.assert_awaited_once_with(1, "only text, no item")


# ---- _item_tool_args: openai Responses API arguments(JSON str)→ dict ----
# 根因(2026-07-15 deepseek-openai 真机): ResponseFunctionToolCall.arguments 注解为
# str(JSON 字符串, OpenAI 标准), 但下游 humanize_tool_call / file_renderer._tool 要求
# dict(isinstance(e.parameters, dict) else {}), str 被当非 dict → workflow.log [TOOL] 行
# 参数空。claude 侧 block.input 已是 dict 不受影响。修复: _item_tool_args 解析 JSON str。

def _tool_call_item(arguments):
    """openai tool_call_item: raw_item.arguments 是 JSON str(Responses API 标准)。"""
    item = MagicMock()
    item.type = "tool_call_item"
    item.raw_item = MagicMock()
    item.raw_item.arguments = arguments
    return item


def test_item_tool_args_parses_json_string_to_dict():
    """arguments 是 JSON str → 解析成 dict(对齐 claude block.input 的 dict 语义)。"""
    item = _tool_call_item('{"command": "ls -la", "description": "list files"}')
    assert _item_tool_args(item) == {"command": "ls -la", "description": "list files"}


def test_item_tool_args_invalid_json_returns_empty_dict():
    """arguments 非法 JSON → {}(不抛错, 兜底对齐 dict 语义)。"""
    assert _item_tool_args(_tool_call_item("not valid json")) == {}


def test_item_tool_args_none_returns_empty_dict():
    """arguments 缺失/None/input 均空 → {}。"""
    item = MagicMock()
    item.raw_item = MagicMock()
    item.raw_item.arguments = None
    item.raw_item.input = None
    assert _item_tool_args(item) == {}


@pytest.mark.asyncio
async def test_tool_call_passes_parsed_dict_to_audit():
    """collector 把 JSON str arguments 解析成 dict 传给 audit(render/humanize 只认 dict)。

    回归: 修复前传的是 str → file_renderer._tool 的 isinstance(dict) 失败 → [TOOL] 行空。
    """
    audit = AsyncMock()
    collector = StreamCollector(audit)
    item = _tool_call_item('{"command": "grep foo bar.py"}')
    ev = MagicMock()
    ev.type = "run_item_stream_event"
    ev.name = "tool_called"
    ev.item = item
    await collector.on_event(ev)
    audit.log_tool_start.assert_awaited_once()
    _, params = audit.log_tool_start.await_args.args
    assert params == {"command": "grep foo bar.py"}  # dict, 非 JSON str
