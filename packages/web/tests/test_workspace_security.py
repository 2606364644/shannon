from __future__ import annotations

import sqlite3

import pytest
from starlette.testclient import TestClient

from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def admin_client(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    app = create_app()
    app.state.auth_store.create_user("admin", hash_password("admin-pw"), role="admin")
    client = TestClient(app, raise_server_exceptions=False)
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    client.post("/api/auth/login", json={"username": "admin", "password": "admin-pw"},
                headers={"X-CSRF-Token": token})
    return client, app


def _csrf(client: TestClient) -> str:
    return client.cookies.get("sn-csrf") or client.get("/api/auth/csrf").json()["csrf_token"]


@pytest.mark.parametrize("name", ["foo/../../outside", "/tmp/outside", "foo\\..\\outside"])
def test_create_workspace_rejects_path_escape(admin_client, name):
    client, app = admin_client
    response = client.post("/api/workspaces", json={"name": name},
                           headers={"X-CSRF-Token": _csrf(client)})

    assert response.status_code == 422
    assert not (app.state.config.workspaces_dir.parent / "outside").exists()


def test_create_user_rolls_back_when_workspace_provision_fails(admin_client, monkeypatch):
    client, app = admin_client

    def fail(*_args, **_kwargs):
        raise sqlite3.OperationalError("membership write failed")

    import supernova_web.api.users as users_api
    monkeypatch.setattr(users_api, "ensure_user_workspace", fail)
    response = client.post("/api/users", json={"username": "alice", "password": "alice-pw-1"},
                           headers={"X-CSRF-Token": _csrf(client)})

    assert response.status_code == 500
    assert app.state.auth_store.get_user_by_username("alice") is None
    assert not (app.state.config.workspaces_dir / "alice").exists()


def test_create_user_rolls_back_when_global_admin_reconciliation_fails(admin_client, monkeypatch):
    client, app = admin_client

    def fail(*_args, **_kwargs):
        raise sqlite3.OperationalError("global reconciliation failed")

    import supernova_web.api.users as users_api
    monkeypatch.setattr(users_api, "ensure_global_admin_access", fail)
    response = client.post("/api/users", json={"username": "alice", "password": "alice-pw-1"},
                           headers={"X-CSRF-Token": _csrf(client)})

    assert response.status_code == 500
    assert app.state.auth_store.get_user_by_username("alice") is None
    assert not (app.state.config.workspaces_dir / "alice").exists()


def test_symlink_workspace_is_not_listed_or_assigned(admin_client, tmp_workspaces, tmp_path):
    client, app = admin_client
    outside = tmp_path / "outside-workspace"
    outside.mkdir()
    (outside / "workspace.json").write_text(
        '{"name":"outside-workspace","owner":"external"}'
    )
    link = tmp_workspaces / "linked"
    link.symlink_to(outside, target_is_directory=True)

    response = client.get("/api/workspaces")

    assert response.status_code == 200
    assert "linked" not in {row["name"] for row in response.json()}
    admin = app.state.auth_store.get_user_by_username("admin")
    assert app.state.auth_store.get_workspace_member_role("linked", admin.id) is None
