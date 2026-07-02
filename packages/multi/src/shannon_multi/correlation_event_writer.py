"""联动编排器层进度事件 writer：把 repo/phase/edge 级状态写到 correlation workspace 的 ndjson。

asyncio.Lock 保护 append（多 repo 顺序 + edge 并发需串行化）。
诚实局限：edge 内部 agent 细粒度事件不进 ndjson（AgentExecutor 不经 dispatcher）。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import aiofiles


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CorrelationEventWriter:
    def __init__(self, ndjson_path: Path) -> None:
        self._path = Path(ndjson_path)
        self._lock = asyncio.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def _append(self, payload: dict) -> None:
        async with self._lock:
            async with aiofiles.open(self._path, "a") as fh:
                await fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
                await fh.flush()

    async def repo(self, name: str, status: str, detail: str | None = None) -> None:
        await self._append({"ts": _now_iso(), "category": "CONTROL",
                            "type": "correlation_progress", "node": "repo",
                            "name": name, "status": status, "detail": detail})

    async def phase(self, name: str, status: str) -> None:
        await self._append({"ts": _now_iso(), "category": "CONTROL",
                            "type": "correlation_progress", "node": "phase",
                            "name": name, "status": status})

    async def edge(self, name: str, status: str, detail: str | None = None) -> None:
        await self._append({"ts": _now_iso(), "category": "CONTROL",
                            "type": "correlation_progress", "node": "edge",
                            "name": name, "status": status, "detail": detail})

    async def scan_end(self, status: str) -> None:
        await self._append({"ts": _now_iso(), "category": "CONTROL",
                            "type": "scan_end", "status": status})
