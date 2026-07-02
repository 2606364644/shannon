# packages/web/tests/test_api_events.py
import json

import httpx
import pytest


@pytest.mark.asyncio
async def test_sse_streams_until_scan_end(app_with_ws, tmp_workspaces):
    ws = tmp_workspaces / "E"
    ws.mkdir()
    ef = ws / "events.ndjson"
    ef.write_text(
        json.dumps({"type": "InfoEvent", "ts": "t1", "category": "INFO", "message": "hi"}) + "\n"
        + json.dumps({"type": "scan_end", "ts": "t2", "category": "CONTROL", "status": "completed"}) + "\n"
    )
    transport = httpx.ASGITransport(app=app_with_ws)
    lines: list[str] = []
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=5) as client:
        async with client.stream("GET", "/api/workspaces/E/events") as r:
            assert r.status_code == 200
            async for line in r.aiter_lines():
                lines.append(line)
                if line.startswith("data:") and "scan_end" in line:
                    break
    assert any("data:" in l and "hi" in l for l in lines)
    assert any("id:" in l for l in lines)  # 带事件 id（Last-Event-ID 用）


@pytest.mark.asyncio
async def test_sse_404_unknown_workspace(app_with_ws):
    transport = httpx.ASGITransport(app=app_with_ws)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/workspaces/nope/events")
        assert r.status_code == 404
