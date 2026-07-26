"""根因修复测试：cookie Secure 标志应按实际 scheme 自适应。

历史 bug：cookie_secure 默认 True，而 main() 用纯 HTTP 启动 uvicorn（无 TLS）。
用户经 http://127.0.0.1:7878 访问时，sn-sid/sn-csrf 带 Secure 标志会被浏览器
按规范丢弃 → 登录成功但 session cookie 没存 → 首页首个业务 API 401 →
client.ts 的 onUnauthorized 跳 /login?expired=1 → 整页刷新丢 React 状态 →
用户被迫重登 → 循环。连 csrf cookie 也受影响（login 直接 403）。

修复策略：
- env SUPERNOVA_WEB_COOKIE_SECURE=1 强制 secure（生产显式安全）
- 否则按请求实际 scheme（含反代 X-Forwarded-Proto）——HTTPS 才 secure
"""
import pytest
from starlette.testclient import TestClient

from supernova_web.auth.passwords import hash_password


@pytest.fixture
def app_factory(tmp_workspaces, monkeypatch):
    """build app，可控 SUPERNOVA_WEB_COOKIE_SECURE env。

    tmp_workspaces 把 SUPERNOVA_WORKER_ROOT 设成 tmp/workspaces（会嵌套一层），
    这里改回 parent 使 resolve_workspaces_dir() == tmp_workspaces，对齐 app_with_ws。
    """
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    from supernova_core.utils.paths import resolve_workspaces_dir
    assert resolve_workspaces_dir() == tmp_workspaces

    def make(secure_env=None):
        from supernova_web import config as cfg_mod
        if secure_env is None:
            monkeypatch.delenv("SUPERNOVA_WEB_COOKIE_SECURE", raising=False)
        else:
            monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", secure_env)
        cfg_mod.get_config.cache_clear()
        from supernova_web.app import create_app
        return create_app()

    return make


def _cookie_header(r, name):
    # TestClient 返回 httpx.Response，headers 是 httpx.Headers（get_list）；
    # 兼容 starlette.datastructures.Headers（getlist）。
    h = r.headers
    raw = h.get_list("set-cookie") if hasattr(h, "get_list") else h.getlist("set-cookie")
    for line in raw:
        if line.startswith(f"{name}="):
            return line
    pytest.fail(f"{name} cookie 未设置")


def test_default_http_csrf_cookie_not_secure(app_factory):
    """默认配置 + HTTP：sn-csrf 不带 Secure（浏览器才会存储 → csrf 链路可用）。"""
    c = TestClient(app_factory(secure_env=None))
    r = c.get("/api/auth/csrf")
    assert r.status_code == 200
    assert "Secure" not in _cookie_header(r, "sn-csrf")


def test_forwarded_https_sets_secure(app_factory):
    """反代 HTTPS（X-Forwarded-Proto=https）：sn-csrf 自动带 Secure。"""
    c = TestClient(app_factory(secure_env="0"))
    r = c.get("/api/auth/csrf", headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 200
    assert "Secure" in _cookie_header(r, "sn-csrf")


def test_env_force_secure_over_http(app_factory):
    """env=1 强制 secure，即便 HTTP 也带 Secure（生产显式安全，不回归）。"""
    c = TestClient(app_factory(secure_env="1"))
    r = c.get("/api/auth/csrf")
    assert r.status_code == 200
    assert "Secure" in _cookie_header(r, "sn-csrf")


def test_login_session_cookie_not_secure_over_http(app_factory):
    """端到端复现根因：默认 HTTP 下 login 链路通 + sn-sid 不带 Secure。

    默认 secure=True 时，csrf cookie 带 Secure → 浏览器丢弃 → login 403；
    修复后默认按 scheme（HTTP）→ 不 secure → login 200 且 sn-sid 可存储。
    """
    app = app_factory(secure_env=None)
    app.state.auth_store.create_user("tester", hash_password("pw"), role="admin")
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    r = c.post(
        "/api/auth/login",
        json={"username": "tester", "password": "pw"},
        headers={"X-CSRF-Token": tok},
    )
    assert r.status_code == 200, r.text
    assert "Secure" not in _cookie_header(r, "sn-sid")
