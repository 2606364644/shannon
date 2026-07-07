from fastapi.testclient import TestClient

from shannon_web.app import create_app


def test_health_ok():
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    git = body["git"]
    assert isinstance(git["binary_available"], bool)
    assert isinstance(git["credentials_configured"], bool)
