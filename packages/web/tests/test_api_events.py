# packages/web/tests/test_api_events.py
"""scan-scoped events SSE（GET /{ws}/scans/{scan_id}/events）。

旧 ws-scoped GET /{ws}/events shim 已移除（联合验收确认零前端调用）。
"""
import json

import httpx
import pytest


@pytest.fixture
def _authed_cookies(app_with_ws, monkeypatch):
    """返 (app, cookies) 供 ASGITransport 用（admin tester，注入 session cookie）。"""
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    app = app_with_ws
    store = app.state.auth_store
    if store.get_user_by_username("tester") is None:
        store.create_user("tester", __import__("supernova_web.auth.passwords", fromlist=["hash_password"]).hash_password("test-pw"), role="admin")
    sid = app.state.session_manager.create(store.get_user_by_username("tester").id)
    return app, {"sn-sid": sid}


def _make_scan(tmp_workspaces, ws="E", scan_id="20260727-120000"):
    scan_dir = tmp_workspaces / ws / "scans" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "session.json").write_text(json.dumps(
        {"status": "running", "scan_type": "whitebox", "created_at": 1}))
    return scan_dir


@pytest.mark.asyncio
async def test_sse_streams_until_scan_end(_authed_cookies, tmp_workspaces):
    """GET /{ws}/scans/{scan_id}/events tail events.ndjson 直到 scan_end。"""
    app, cookies = _authed_cookies
    scan_dir = _make_scan(tmp_workspaces)
    ef = scan_dir / "events.ndjson"
    ef.write_text(
        json.dumps({"type": "InfoEvent", "ts": "t1", "category": "INFO", "message": "hi"}) + "\n"
        + json.dumps({"type": "scan_end", "ts": "t2", "category": "CONTROL", "status": "completed"}) + "\n"
    )
    transport = httpx.ASGITransport(app=app)
    lines: list[str] = []
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=5, cookies=cookies) as client:
        async with client.stream("GET", "/api/workspaces/E/scans/20260727-120000/events") as r:
            assert r.status_code == 200
            async for line in r.aiter_lines():
                lines.append(line)
                if line.startswith("data:") and "scan_end" in line:
                    break
    assert any("data:" in l and "hi" in l for l in lines)
    assert any("id:" in l for l in lines)  # 带事件 id（Last-Event-ID 用）


@pytest.mark.asyncio
async def test_sse_404_unknown_scan(_authed_cookies, tmp_workspaces):
    """scan 不存在 -> 404。"""
    app, cookies = _authed_cookies
    _make_scan(tmp_workspaces, scan_id="s1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", cookies=cookies) as client:
        r = await client.get("/api/workspaces/E/scans/nope/events")
        assert r.status_code == 404
