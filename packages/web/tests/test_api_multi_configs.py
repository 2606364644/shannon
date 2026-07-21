from fastapi.testclient import TestClient

from supernova_web.app import create_app

_VALID = """\
repos:
  svc-a:
    path: /code/a
    role: entrypoint
correlation:
  out_workspace: cor-out
"""


def test_crud(app_with_ws, tmp_workspaces):
    client = TestClient(app_with_ws)
    assert client.post("/api/multi-configs", json={"name": "demo", "content": _VALID}).status_code == 201
    assert "demo" in client.get("/api/multi-configs").json()
    got = client.get("/api/multi-configs/demo").json()
    assert got["content"] == _VALID


def test_invalid_returns_422(app_with_ws):
    client = TestClient(app_with_ws)
    r = client.post("/api/multi-configs", json={"name": "bad", "content": "repos: not-a-mapping\n"})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)
