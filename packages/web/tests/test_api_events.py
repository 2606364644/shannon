# packages/web/tests/test_api_events.py
import json

import httpx
import pytest


@pytest.fixture
def _authed_cookies(app_with_ws, monkeypatch):
    """T11 后 /api/workspaces/*/events 要求登录；返 (app, cookies) 供 ASGITransport 用。

    ASGITransport 不走 TestClient，需手动把 sn-sid cookie 注入 AsyncClient。
    cookie_secure env 必须在 create_app 之前设好（get_config lru_cache）。
    """
    from supernova_core.utils.paths import resolve_workspaces_dir
    # app_with_ws 已经 remount SUPERNOVA_WORKER_ROOT；此处仅补 cookie_secure
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    # app_with_ws 已通过 create_app() 缓存了 cfg；重新清缓存、并 reset app_with_ws 不可行，
    # 故改用其返回的 app 但绕过 cfg.cookie_secure：直接在 store 里建 session 注入 cookie。
    from starlette.testclient import TestClient
    from supernova_web.auth.passwords import hash_password
    app = app_with_ws
    store = app.state.auth_store
    if store.get_user_by_username("tester") is None:
        store.create_user("tester", hash_password("test-pw"), role="admin")
    # 直接走 /api/auth/login（TestClient 在 cookie_secure=0 后才能跑）——
    # 但 cfg 已 cached；改用直接生成 session 注入：
    sid = app.state.session_manager.create(
        store.get_user_by_username("tester").id
    )
    return app, {"sn-sid": sid}


@pytest.mark.asyncio
async def test_sse_streams_until_scan_end(_authed_cookies, tmp_workspaces):
    app, cookies = _authed_cookies
    ws = tmp_workspaces / "E"
    ws.mkdir()
    ef = ws / "events.ndjson"
    ef.write_text(
        json.dumps({"type": "InfoEvent", "ts": "t1", "category": "INFO", "message": "hi"}) + "\n"
        + json.dumps({"type": "scan_end", "ts": "t2", "category": "CONTROL", "status": "completed"}) + "\n"
    )
    transport = httpx.ASGITransport(app=app)
    lines: list[str] = []
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=5, cookies=cookies) as client:
        async with client.stream("GET", "/api/workspaces/E/events") as r:
            assert r.status_code == 200
            async for line in r.aiter_lines():
                lines.append(line)
                if line.startswith("data:") and "scan_end" in line:
                    break
    assert any("data:" in l and "hi" in l for l in lines)
    assert any("id:" in l for l in lines)  # 带事件 id（Last-Event-ID 用）


@pytest.mark.asyncio
async def test_sse_404_unknown_workspace(_authed_cookies):
    app, cookies = _authed_cookies
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", cookies=cookies) as client:
        r = await client.get("/api/workspaces/nope/events")
        assert r.status_code == 404
