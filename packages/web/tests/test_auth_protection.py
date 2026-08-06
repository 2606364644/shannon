import pytest
from starlette.testclient import TestClient

from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def app_and_client(tmp_workspaces, monkeypatch):
    # cascade from T10 fixture remount：tmp_workspaces 把 SUPERNOVA_WORKER_ROOT 设成
    # tmp_path/workspaces，resolve_workspaces_dir 再追加 /workspaces → 嵌套一层；
    # 改回父目录使 cfg.workspaces_dir == tmp_workspaces，auth.db 才能落到存在目录里。
    from supernova_core.utils.paths import resolve_workspaces_dir
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    assert resolve_workspaces_dir() == tmp_workspaces
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    app = create_app()
    app.state.auth_store.create_user("alice", hash_password("pw123"))
    c = TestClient(app)
    return app, c


def _login(c):
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post(
        "/api/auth/login",
        json={"username": "alice", "password": "pw123"},
        headers={"X-CSRF-Token": tok},
    )


def test_protected_routes_require_auth(app_and_client):
    _, c = app_and_client
    # P2: repos 路由迁到 /api/workspaces/{ws}/repos 下（/api/repos 已不存在）。
    # router 仍挂 dependencies=_require_auth，未登录访问任一 repos 子路径都应 401。
    for path in ["/api/workspaces", "/api/workspaces/ws1/repos", "/api/multi-configs", "/api/system-status"]:
        assert c.get(path).status_code == 401, path


def test_health_public(app_and_client):
    _, c = app_and_client
    assert c.get("/health").status_code == 200


def test_authed_can_access(app_and_client):
    _, c = app_and_client
    _login(c)
    r = c.get("/api/workspaces")
    assert r.status_code != 401  # 登录后不再 401（可能 200 空列表）


def test_startup_seeds_users(tmp_path, tmp_workspaces, monkeypatch):
    yaml_path = tmp_path / "users.yaml"
    yaml_path.write_text(
        'users:\n  - username: admin\n    password_hash: "$2b$12$x"\n    role: admin\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SUPERNOVA_WEB_USERS_SEED", str(yaml_path))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    # cascade from T10 fixture remount：使 cfg.workspaces_dir == tmp_workspaces
    from supernova_core.utils.paths import resolve_workspaces_dir
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    assert resolve_workspaces_dir() == tmp_workspaces
    app = create_app()
    with TestClient(app):  # 触发 lifespan startup → seed
        pass
    assert app.state.auth_store.get_user_by_username("admin") is not None


def test_startup_seeds_system_auth_profiles(tmp_path, tmp_workspaces, monkeypatch):
    # configs/*.yaml 的 authentication 段 → 启动 seed 成系统档案（.system 段）
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "demo.yaml").write_text(
        'authentication:\n  login_type: form\n  login_url: "http://x/l"\n'
        '  credentials:\n    username: "u"\n    password: "p"\n'
        '  login_flow:\n    - "step one"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SUPERNOVA_CONFIGS_DIR", str(configs))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_core.utils.paths import resolve_workspaces_dir
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    assert resolve_workspaces_dir() == tmp_workspaces
    app = create_app()
    with TestClient(app):  # 触发 lifespan → seed configs → .system
        pass
    names = {p.name for p in app.state.auth_profile_store.read(".system")}
    assert "demo" in names
