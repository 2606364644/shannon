# packages/web/src/supernova_web/api/events.py
"""scan events SSE 构造（build_scan_events_response）。

旧 ws-scoped GET /{ws}/events shim 已移除（Phase 2 前端切 scan-scoped
GET /{ws}/scans/{scan_id}/events，零前端调用，联合验收确认）。scan-scoped 路由在
api/scans.py，调本模块的 build_scan_events_response。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Request
from fastapi.responses import StreamingResponse

from supernova_web.components.event_tailer import EventTailer


async def build_scan_events_response(request: Request, scan_dir: Path) -> StreamingResponse:
    """构造 scan events SSE StreamingResponse（tail scan_dir/events.ndjson + 孤儿对账）。

    scans.py 的 GET /{ws}/scans/{scan_id}/events 调本函数。孤儿对账 per-scan：scan 非在跑
    + 无 scan_end -> 补 scan_end（让 SSE 立即有关流信号 + 失败原因，而非空等 idle_timeout
    后关流再被前端重连死循环）。
    """
    ndjson = scan_dir / "events.ndjson"

    idx = request.app.state.indexer
    idx.sync_active(request.app.state.scan_manager.active_pids())
    # 判活 per-scan（heartbeat），非 ws 级 pid（C1 容器无本机 pid）。
    from supernova_core.session import SessionManager
    from supernova_web.components.workspaces_indexer import _compute_status
    mgr = SessionManager(scan_dir.parent)
    is_running = _compute_status(scan_dir, mgr.get_status(scan_dir)) == "running"
    if not is_running:
        from supernova_web.components.orphan_reconciler import reconcile_orphaned
        await reconcile_orphaned(scan_dir, False)

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


async def build_verify_events_response(
    ndjson: Path, last_event_id: int | None = None,
) -> StreamingResponse:
    """构造 auth 验证过程 SSE（tail ndjson + 遇 scan_end 关流）。

    与 build_scan_events_response 同源（EventTailer + scan_end sentinel），但**无孤儿对账**
    ——auth 验证是独立短 workflow（AuthValidationWorkflow），其 scan_end 由 finalize_summary
    写；worker 崩溃致 scan_end 缺失时，前端 useEventSource 的 onerror 重连 + verify-status
    轮询兜底（spec §8 降级）。last_event_id 支持 Last-Event-ID 断点续传。
    """
    async def gen():
        queue: asyncio.Queue = asyncio.Queue()
        tailer = EventTailer(ndjson)

        async def on_event(data: dict, event_id: int):
            await queue.put(EventTailer.encode_sse(data, event_id))
            if data.get("type") == "scan_end":
                await queue.put(None)  # sentinel：关流

        task = asyncio.create_task(tailer.tail(on_event, last_event_id=last_event_id))
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
