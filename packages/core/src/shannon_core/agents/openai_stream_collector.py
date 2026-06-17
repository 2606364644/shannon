"""openai-agents 流式 event 收集器：实时驱动逐轮 audit + 累积 text/turns。

对齐 anthropic 侧 MessageDispatcher 的逐轮上报语义：
- 每个 agent_updated_stream_event 开启一个新 turn；
- turn 内的文本累积，turn 结束（下一个 agent_updated 或流结束）时上报 log_assistant_turn；
- tool 调用 → log_tool_start，tool 输出 → log_tool_end。
"""
from __future__ import annotations

from typing import Any

from openai.types.responses import ResponseTextDeltaEvent

from .tool_audit_logger import ToolAuditLogger


class StreamCollector:
    def __init__(self, audit_logger: ToolAuditLogger | None):
        self._audit = audit_logger
        self._turn_count = 0
        self._turn_text = ""
        self._all_text: list[str] = []
        self.tool_call_count = 0

    @property
    def turns(self) -> int:
        return self._turn_count

    @property
    def text(self) -> str:
        return "".join(self._all_text)

    async def on_event(self, event: Any) -> None:
        etype = getattr(event, "type", None)

        if etype == "agent_updated_stream_event":
            await self._close_turn()
            self._turn_count += 1
            return

        if etype == "raw_response_event":
            data = getattr(event, "data", None)
            if isinstance(data, ResponseTextDeltaEvent):
                delta = getattr(data, "delta", "") or ""
                self._turn_text += delta
                self._all_text.append(delta)
            return

        if etype == "run_item_stream_event":
            name = getattr(event, "name", None)
            item = getattr(event, "item", None)
            item_type = getattr(item, "type", None)
            if name == "tool_called" or item_type == "tool_call_item":
                self.tool_call_count += 1
                if self._audit is not None:
                    await self._audit.log_tool_start(_item_tool_name(item), _item_tool_args(item))
            elif name == "tool_output" or item_type == "tool_call_output_item":
                if self._audit is not None:
                    await self._audit.log_tool_end(getattr(item, "output", ""))
            return

    async def close(self) -> None:
        await self._close_turn()

    async def _close_turn(self) -> None:
        if self._turn_count > 0 and self._turn_text and self._audit is not None:
            await self._audit.log_assistant_turn(self._turn_count, self._turn_text)
        self._turn_text = ""


def _item_tool_name(item: Any) -> str:
    raw = getattr(item, "raw_item", None)
    return getattr(raw, "name", None) or getattr(item, "name", None) or "tool"


def _item_tool_args(item: Any) -> Any:
    raw = getattr(item, "raw_item", None)
    return getattr(raw, "arguments", None) or getattr(raw, "input", None) or {}
