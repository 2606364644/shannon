import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from supernova_web.app import create_app
from supernova_web.models import ScanRequest


def _app_with_repos(tmp_path, monkeypatch, repos_state):
    monkeypatch.setenv("SUPERNOVA_REPOS_DIR", str(tmp_path / "repos"))
    (tmp_path / "repos").mkdir(parents=True, exist_ok=True)
    for name, state in repos_state.items():
        d = tmp_path / "repos" / name; d.mkdir()
        (d / ".supernova-repo.json").write_text(json.dumps({"name": name, "state": state}))
    # T11 auth cascade: auth.db 落在 workspaces_dir/auth.db，必须先建好该目录，
    # 否则 AuthStore.init_schema() → sqlite3.OperationalError。
    (tmp_path / "workspaces").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_web.config import get_config; get_config.cache_clear()
    return create_app()


def _authed(app):
    """构造已登录的 TestClient（仅 test_scan_git_kind_422 用）。"""
    from supernova_web.auth.passwords import hash_password
    store = app.state.auth_store
    if store.get_user_by_username("tester") is None:
        store.create_user("tester", hash_password("test-pw"))
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": "tester", "password": "test-pw"},
           headers={"X-CSRF-Token": tok})
    return c


def test_resolve_repo_ready(tmp_path, monkeypatch):
    app = _app_with_repos(tmp_path, monkeypatch, {"foo": "ready"})
    target, _ = asyncio.run(
        app.state.scan_manager._resolve_inputs(
            ScanRequest(type="whitebox", source={"kind": "repo", "value": "foo"})))
    assert target.endswith("repos/foo")


def test_resolve_repo_cloning_raises(tmp_path, monkeypatch):
    app = _app_with_repos(tmp_path, monkeypatch, {"foo": "cloning"})
    with pytest.raises(ValueError, match="未就绪"):
        asyncio.run(
            app.state.scan_manager._resolve_inputs(
                ScanRequest(type="whitebox", source={"kind": "repo", "value": "foo"})))


def test_resolve_repo_missing_raises(tmp_path, monkeypatch):
    app = _app_with_repos(tmp_path, monkeypatch, {"foo": "ready"})
    with pytest.raises(ValueError):
        asyncio.run(
            app.state.scan_manager._resolve_inputs(
                ScanRequest(type="whitebox", source={"kind": "repo", "value": "nope"})))


def test_scan_git_kind_422(tmp_path, monkeypatch):
    app = _app_with_repos(tmp_path, monkeypatch, {})
    client = _authed(app)
    tok = client.get("/api/auth/csrf").json()["csrf_token"]
    r = client.post("/api/scan", json={
        "type": "whitebox", "source": {"kind": "git", "value": "https://x.git"}, "url": "http://x"},
        headers={"X-CSRF-Token": tok})
    assert r.status_code == 422
