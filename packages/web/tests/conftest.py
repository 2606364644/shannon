import os
import sys
from pathlib import Path

import pytest

# 确保 src 在 path（开发期非 wheel 安装）
_ROOT = Path(__file__).resolve().parents[3]
for member in ("src",):
    p = _ROOT / "packages" / "web" / member
    if p.is_dir():
        sys.path.insert(0, str(p))


@pytest.fixture
def tmp_workspaces(tmp_path, monkeypatch):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(ws))
    return ws


@pytest.fixture(autouse=True)
def _reset_config():
    """清 get_config lru_cache，使每个测试读到当前 env（tmp_workspaces 等）。"""
    from supernova_web import config as cfg_mod
    cfg_mod.get_config.cache_clear()
    yield
    cfg_mod.get_config.cache_clear()


@pytest.fixture
def app_with_ws(tmp_workspaces, monkeypatch):
    """create_app() 持单例，且让 get_config().workspaces_dir == tmp_workspaces。

    tmp_workspaces fixture 把 SUPERNOVA_WORKER_ROOT 设成 tmp_path/"workspaces"，
    而 resolve_workspaces_dir() 会再追加 /"workspaces" → 嵌套一层。此处把
    SUPERNOVA_WORKER_ROOT 改成父目录，使解析结果恰好等于 tmp_workspaces，
    这样测试在 tmp_workspaces 下直接建 ws 目录即可被 indexer 命中。

    T11 起同时把 SUPERNOVA_WEB_COOKIE_SECURE 关掉——所有用此 fixture 的测试
    走 TestClient（HTTP），Secure 标志会让 cookie 不发送致登录/CSRF 失败。
    """
    from supernova_core.utils.paths import resolve_workspaces_dir
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    assert resolve_workspaces_dir() == tmp_workspaces
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_web.app import create_app
    return create_app()


@pytest.fixture
def authed_client(app_with_ws, monkeypatch):
    """已登录的 TestClient（用户 tester/test-pw），供需要鉴权的现有测试迁移用。

    委托 app_with_ws（已有 remount + cookie_secure=0）；此处仅补「建用户 + 登录」。
    """
    from starlette.testclient import TestClient
    from supernova_web.auth.passwords import hash_password
    app = app_with_ws
    app.state.auth_store.create_user("tester", hash_password("test-pw"))
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post(
        "/api/auth/login",
        json={"username": "tester", "password": "test-pw"},
        headers={"X-CSRF-Token": tok},
    )
    return c
