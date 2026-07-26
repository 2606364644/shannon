import pytest
from fastapi.testclient import TestClient

from supernova_web.app import create_app
from supernova_web.components.scan_manager import TemporalUnavailable, TooManyScans


class FakeSM:
    def __init__(self):
        self.started = []
        self.exc = None
        self.cancelled = []

    async def start(self, req):
        if self.exc:
            raise self.exc
        self.started.append(req)
        return "WSX"

    async def cancel(self, ws):
        self.cancelled.append(ws)
        return True

    def active_pids(self):
        return {}


_BODY = {"type": "whitebox", "source": {"kind": "path", "value": "/x"}, "url": "http://e",
        "workspace": "WSX"}


@pytest.fixture
def _authed_app(tmp_workspaces, monkeypatch):
    """T11 后 /api/scan 要求登录 + 写操作 CSRF；返 (app, csrf_getter)。

    create_app 之前需把 cookie_secure 关掉（get_config lru_cache）。

    Task 4 起 /api/scan 还要求 ws 已存在 + 当前用户成员/admin。tester 改为 admin
    + 预建 WSX 目录, 使现有 6 个测试 (测 endpoint 错误处理, 非测成员) 不受影响。
    成员语义由 test_workspace_lifecycle.py 覆盖。
    """
    from supernova_core.utils.paths import resolve_workspaces_dir
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    assert resolve_workspaces_dir() == tmp_workspaces
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_web.auth.passwords import hash_password
    app = create_app()
    app.state.auth_store.create_user("tester", hash_password("test-pw"), role="admin")
    # 预建 WSX 目录, 使 create_scan 的 ws-exists 校验通过; FakeSM.start 仍返 "WSX"。
    # per-test create_app(overrides=...) 共享同一 workspaces_dir (经 env), WSX 可见。
    app.state.config.workspaces_dir.joinpath("WSX").mkdir(parents=True, exist_ok=True)
    return app


def _authed_client(app):
    """构造已登录的 TestClient（业务路由要走 HTTP，cookie_secure 已关）。"""
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": "tester", "password": "test-pw"},
           headers={"X-CSRF-Token": tok})
    return c


def _csrf(c):
    return c.get("/api/auth/csrf").json()["csrf_token"]


def test_post_scan_202(_authed_app):
    fake = FakeSM()
    app = create_app(overrides={"scan_manager": fake})
    # 把 _authed_app 的 auth_store/session_manager 复用过来（同 db 文件）
    app.state.auth_store = _authed_app.state.auth_store
    app.state.session_manager = _authed_app.state.session_manager
    client = _authed_client(app)
    tok = _csrf(client)
    r = client.post("/api/scan", json=_BODY, headers={"X-CSRF-Token": tok})
    assert r.status_code == 202
    assert r.json() == {"workspace": "WSX"}
    assert len(fake.started) == 1


def test_post_scan_400_temporal(_authed_app):
    fake = FakeSM()
    fake.exc = TemporalUnavailable()
    app = create_app(overrides={"scan_manager": fake})
    app.state.auth_store = _authed_app.state.auth_store
    app.state.session_manager = _authed_app.state.session_manager
    client = _authed_client(app)
    tok = _csrf(client)
    assert client.post("/api/scan", json=_BODY, headers={"X-CSRF-Token": tok}).status_code == 400


def test_post_scan_409_concurrent(_authed_app):
    fake = FakeSM()
    fake.exc = TooManyScans(1)
    app = create_app(overrides={"scan_manager": fake})
    app.state.auth_store = _authed_app.state.auth_store
    app.state.session_manager = _authed_app.state.session_manager
    client = _authed_client(app)
    tok = _csrf(client)
    assert client.post("/api/scan", json=_BODY, headers={"X-CSRF-Token": tok}).status_code == 409


def test_delete_scan(_authed_app):
    fake = FakeSM()
    app = create_app(overrides={"scan_manager": fake})
    app.state.auth_store = _authed_app.state.auth_store
    app.state.session_manager = _authed_app.state.session_manager
    client = _authed_client(app)
    tok = _csrf(client)
    assert client.delete("/api/scan/WSX", headers={"X-CSRF-Token": tok}).status_code == 200


def test_cancel_passes_through_via_signal(_authed_app):
    """cancel 对 owner=host scan 返 via:signal 时,api 透传给前端(语义提示)。"""
    class HostSM:
        async def cancel(self, ws):
            return {"cancelled": ws, "via": "signal"}

        def active_pids(self):
            return {}

    app = create_app(overrides={"scan_manager": HostSM()})
    app.state.auth_store = _authed_app.state.auth_store
    app.state.session_manager = _authed_app.state.session_manager
    client = _authed_client(app)
    tok = _csrf(client)
    r = client.delete("/api/scan/WSX", headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    assert r.json() == {"cancelled": "WSX", "via": "signal"}


def test_cancel_404_when_workspace_missing(_authed_app):
    """workspace 不存在(scan_manager.cancel 返 None)→ 唯一 404(spec §4.6)。"""
    class NoScanSM:
        async def cancel(self, ws):
            return None

        def active_pids(self):
            return {}

    app = create_app(overrides={"scan_manager": NoScanSM()})
    app.state.auth_store = _authed_app.state.auth_store
    app.state.session_manager = _authed_app.state.session_manager
    client = _authed_client(app)
    tok = _csrf(client)
    assert client.delete("/api/scan/WSX", headers={"X-CSRF-Token": tok}).status_code == 404
