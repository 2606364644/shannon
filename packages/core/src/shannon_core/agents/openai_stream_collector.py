"""openai-agents 流式 event 收集器：实时驱动逐轮 audit + 累积 text/turns。

对齐 anthropic 侧 MessageDispatcher 的逐轮上报语义（每个 assistant 模型响应 = 1 turn）。

turn 计数基于模型响应产出的 run item（tool_call_item / message_output_item），
而非 agent_updated_stream_event——后者在 Chat Completions 模式下整个 run 只发 1 次，
无法区分多轮（实测：真实工具 loop 被错算成 1 turn）。
raw_response_event 的 text delta 累积为当前 turn 的文本，在该 turn 的 run item
出现时作为该 turn 上报 log_assistant_turn。
"""
from __future__ import annotations

import json
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
            # 模型响应产出的 item（调工具 / 给消息）= 一个新 turn
            if name == "tool_called" or item_type == "tool_call_item":
                await self._advance_turn()
                self.tool_call_count += 1
                if self._audit is not None:
                    await self._audit.log_tool_start(_item_tool_name(item), _item_tool_args(item))
            elif name == "message_output_created" or item_type == "message_output_item":
                await self._advance_turn()
            elif name == "tool_output" or item_type == "tool_call_output_item":
                # 工具执行结果，不是模型响应，不计 turn
                if self._audit is not None:
                    await self._audit.log_tool_end(getattr(item, "output", ""))
            return

        # agent_updated_stream_event / 其它：忽略（不再用于计 turn）

    async def close(self) -> None:
        # 流结束，上报最后一 turn 尚未随 run item flush 的尾文本（如有）
        await self._flush_turn()

    async def _advance_turn(self) -> None:
        """新模型响应 turn：turn_count +1，再把累积的文本作为该 turn 上报。"""
        self._turn_count += 1
        await self._flush_turn()

    async def _flush_turn(self) -> None:
        if self._turn_text and self._audit is not None:
            turn_no = self._turn_count or 1
            await self._audit.log_assistant_turn(turn_no, self._turn_text)
        self._turn_text = ""


def _item_tool_name(item: Any) -> str:
    raw = getattr(item, "raw_item", None)
    return getattr(raw, "name", None) or getattr(item, "name", None) or "tool"


def _item_tool_args(item: Any) -> Any:
    raw = getattr(item, "raw_item", None)
    args = getattr(raw, "arguments", None) or getattr(raw, "input", None) or {}
    # openai Responses API 的 arguments 是 JSON 字符串(str, OpenAI 标准), 下游
    # humanize_tool_call / file_renderer._tool 要求 dict(isinstance dict else {}),
    # 未解析会被当非 dict → workflow.log [TOOL] 行参数空(claude 侧 block.input 已是
    # dict 不受影响)。解析失败/空 → {} 兜底, 对齐 dict 语义。
    if isinstance(args, str):
        try:
            return json.loads(args)
        except (json.JSONDecodeError, ValueError):
            return {}
    return args
