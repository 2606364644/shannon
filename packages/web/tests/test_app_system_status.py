from fastapi.testclient import TestClient

from shannon_web.app import create_app


def test_system_status_shape():
    client = TestClient(create_app())
    r = client.get("/api/system-status")
    assert r.status_code == 200
    body = r.json()
    # 顶层字段
    assert body["ai_provider"] in {"claude", "openai"}
    assert body["browser_engine"] in {"agent-browser", "playwright"}
    assert isinstance(body["git_available"], bool)
    assert body["version"].startswith("shannon-web")
    # temporal 子对象
    t = body["temporal"]
    assert t["enabled"] is True
    assert "host" in t and isinstance(t["host"], str)
    assert t["last_status"] in {"connected", "error"}
    assert "last_error" in t
