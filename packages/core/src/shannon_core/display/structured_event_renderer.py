"""Web 事件落盘 renderer：把原子 DisplayEvent 序列化成 ndjson 行。

env SHANNON_WEB_EVENT_FILE 启用（由 workflow_logger.initialize 挂载）。
收到 SummaryEvent 时额外写一行 scan_end 收尾（双路兜底之一，另一路在 web 的 ScanManager）。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from typing import Any

import aiofiles

from shannon_core.display.events import DisplayEvent, SummaryEvent


class StructuredEventRenderer:
    def __init__(self, path: str) -> None:
        self._path = path
        self._fh: Any = None  # lazy open
        self._lock = asyncio.Lock()

    async def _ensure_open(self) -> Any:
        if self._fh is None:
            self._fh = await aiofiles.open(self._path, "a")
        return self._fh

    @staticmethod
    def _serialize(event: DisplayEvent) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ts": event.timestamp,
            "category": event.category,
            "type": type(event).__name__,
        }
        if is_dataclass(event):
            extra = asdict(event)
            extra.pop("timestamp", None)  # 已并入 ts
            extra.pop("category", None)
            payload.update(extra)
        return payload

    async def render(self, event: DisplayEvent) -> None:
        async with self._lock:
            fh = await self._ensure_open()
            await fh.write(json.dumps(self._serialize(event), default=str, ensure_ascii=False) + "\n")
            await fh.flush()
            if isinstance(event, SummaryEvent):
                await fh.write(json.dumps({
                    "ts": event.timestamp,
                    "category": "CONTROL",
                    "type": "scan_end",
                    "status": event.status,
                }, ensure_ascii=False) + "\n")
                await fh.flush()

    async def close(self) -> None:
        async with self._lock:
            if self._fh is not None:
                await self._fh.flush()
                await self._fh.close()
                self._fh = None
