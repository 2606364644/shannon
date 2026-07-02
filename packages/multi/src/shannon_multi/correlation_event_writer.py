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

# edge() 层单点映射：orchestrator 透传的 raw edge 状态（ok/unverified/error）
# → spec L469 钉死的硬契约状态（started|completed|failed）。
# repo()/phase()/scan_end() 已用 spec 值，不动。
_EDGE_STATUS_MAP = {"ok": "completed", "unverified": "completed", "error": "failed"}


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
        mapped = _EDGE_STATUS_MAP.get(status, status)
        if mapped != status:  # 发生映射，保留 raw 进 detail 以便追溯
            detail = f"raw={status}" if detail is None else f"{detail} (raw={status})"
        await self._append({"ts": _now_iso(), "category": "CONTROL",
                            "type": "correlation_progress", "node": "edge",
                            "name": name, "status": mapped, "detail": detail})

    async def scan_end(self, status: str) -> None:
        await self._append({"ts": _now_iso(), "category": "CONTROL",
                            "type": "scan_end", "status": status})
