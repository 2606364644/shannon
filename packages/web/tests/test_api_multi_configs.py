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


def _csrf(c):
    """T11 后写操作（POST）需 X-CSRF-Token header。"""
    return c.get("/api/auth/csrf").json()["csrf_token"]


def test_crud(authed_client, tmp_workspaces):
    client = authed_client
    tok = _csrf(client)
    assert client.post("/api/multi-configs", json={"name": "demo", "content": _VALID},
                       headers={"X-CSRF-Token": tok}).status_code == 201
    assert "demo" in client.get("/api/multi-configs").json()
    got = client.get("/api/multi-configs/demo").json()
    assert got["content"] == _VALID


def test_invalid_returns_422(authed_client):
    client = authed_client
    tok = _csrf(client)
    r = client.post("/api/multi-configs", json={"name": "bad", "content": "repos: not-a-mapping\n"},
                    headers={"X-CSRF-Token": tok})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)
