"""Web 事件落盘 renderer：把原子 DisplayEvent 序列化成 ndjson 行。

env SHANNON_WEB_EVENT_FILE 启用（由 workflow_logger.initialize 挂载）。
收到 SummaryEvent 时额外写一行 scan_end 收尾（双路兜底之一，另一路在 web 的 ScanManager）。
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
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


def wire_web_event_file(workspaces_dir: Path, workspace_name: str | None) -> None:
    """若 SHANNON_WEB_EVENT_FILE 未设,默认指向 <workspaces_dir>/<workspace>/events.ndjson。

    让 CLI(uv run shannon-whitebox start,无 -w)启动的扫描也能在 shannon-web 实时页
    (LiveTab 经 SSE tail events.ndjson)可见:
    - WEB 启动时 scan_manager 已注入该 env → setdefault 不覆盖,行为零变化;
    - CLI 启动 env 未设 → 这里补上 → WorkflowLogger.initialize 挂载 StructuredEventRenderer
      → events.ndjson 持续写入 → SSE 有数据 → 实时页可见。

    由各 track 的 worker 在 workspace 名回填后、Client.connect 前调用(此时 temporal Worker
    尚未构造,后续 activity 在同进程读 env 拿到该值)。workspace_name 为空时不注入(防御:
    调用方此刻应已回填 name)。
    """
    if not workspace_name:
        return
    os.environ.setdefault(
        "SHANNON_WEB_EVENT_FILE",
        str(Path(workspaces_dir) / workspace_name / "events.ndjson"),
    )
