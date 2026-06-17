from unittest.mock import AsyncMock, MagicMock

import pytest
from openai.types.responses import ResponseTextDeltaEvent

from shannon_core.agents.openai_stream_collector import StreamCollector


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
    if item_type == "message_output_item":
        item.raw_item = MagicMock()
        from agents import ItemHelpers
        item._helpers_text = None  # 占位
    ev = MagicMock()
    ev.type = "run_item_stream_event"
    ev.name = name
    ev.item = item
    return ev


def _agent_event():
    ev = MagicMock()
    ev.type = "agent_updated_stream_event"
    ev.new_agent = MagicMock()
    ev.new_agent.name = "A"
    return ev


@pytest.mark.asyncio
async def test_collects_text_and_reports_turn():
    audit = AsyncMock()
    collector = StreamCollector(audit)
    await collector.on_event(_agent_event())  # 新 agent → 新 turn
    await collector.on_event(_text_event("hello "))
    await collector.on_event(_text_event("world"))
    await collector.on_event(_agent_event())  # 再次新 agent → 第二 turn
    await collector.on_event(_text_event("second"))
    await collector.close()  # 流结束，上报最后一个 turn
    assert collector.text == "hello worldsecond"
    assert collector.turns == 2
    audit.log_assistant_turn.assert_any_call(1, "hello world")
    audit.log_assistant_turn.assert_any_call(2, "second")


@pytest.mark.asyncio
async def test_reports_tool_calls():
    audit = AsyncMock()
    collector = StreamCollector(audit)
    await collector.on_event(_run_item_event("tool_call_item", "tool_called"))
    await collector.on_event(_run_item_event("tool_call_output_item", "tool_output", output="result-data"))
    assert collector.tool_call_count == 1
    audit.log_tool_start.assert_awaited()
    audit.log_tool_end.assert_awaited_with("result-data")
