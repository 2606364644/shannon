# packages/web/src/shannon_web/api/events.py
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from shannon_web.components.event_tailer import EventTailer

router = APIRouter(prefix="/api/workspaces", tags=["events"])


@router.get("/{ws}/events")
async def stream_events(ws: str, request: Request):
    cfg = request.app.state.config
    ws_dir = cfg.workspaces_dir / ws
    if not ws_dir.exists():
        raise HTTPException(404, "workspace not found")
    ndjson = ws_dir / "events.ndjson"

    last = request.headers.get("last-event-id")
    last_offset = int(last) if last else None

    async def gen():
        queue: asyncio.Queue = asyncio.Queue()
        tailer = EventTailer(ndjson)

        async def on_event(data: dict, event_id: int):
            await queue.put(EventTailer.encode_sse(data, event_id))
            if data.get("type") == "scan_end":
                await queue.put(None)  # sentinel：关流

        task = asyncio.create_task(tailer.tail(on_event, last_event_id=last_offset))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            task.cancel()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
