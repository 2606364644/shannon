import json

import pytest
from starlette.testclient import TestClient

from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def _setup(tmp_workspaces, monkeypatch):
    # tmp_workspaces 设 SUPERNOVA_WORKER_ROOT=tmp_path/workspaces，但
    # resolve_workspaces_dir() 再追加 /workspaces -> 嵌套一层且不存在，auth.db 打不开。
    # 改回 tmp_workspaces.parent 使 workspaces_dir == tmp_workspaces（conftest app_with_ws 模式）。
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    app = create_app()
    st = app.state.auth_store
    st.create_user("admin", hash_password("p"), role="admin")
    st.create_user("alice", hash_password("p"))
    st.create_user("bob", hash_password("p"))
    # 建两个 workspace 目录（模拟已有产物目录）。
    # 必须写 session.json：SessionManager.list_workspaces() 只返回含 session.json
    # 的目录（packages/core/src/supernova_core/session.py:63），裸目录不可见。
    for name in ("ws_alice", "ws_bob"):
        ws = app.state.config.workspaces_dir / name
        ws.mkdir()
        (ws / "session.json").write_text(json.dumps({
            "status": "completed", "scan_type": "whitebox",
            "created_at": "2026-07-02T10:00:00Z", "completed_at": "2026-07-02T10:05:00Z",
        }))
    st.add_workspace_member("ws_alice", st.get_user_by_username("alice").id, "manager")
    st.add_workspace_member("ws_bob", st.get_user_by_username("bob").id, "manager")
    return app


def _login(app, username):
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": username, "password": "p"}, headers={"X-CSRF-Token": tok})
    return c


def test_alice_sees_only_her_workspace(_setup):
    c = _login(_setup, "alice")
    names = [w["name"] for w in c.get("/api/workspaces").json()]
    assert names == ["ws_alice"]


def test_admin_sees_all(_setup):
    c = _login(_setup, "admin")
    names = sorted(w["name"] for w in c.get("/api/workspaces").json())
    assert names == ["ws_alice", "ws_bob"]
