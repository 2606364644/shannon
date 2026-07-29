from pathlib import Path

from fastapi.testclient import TestClient

from supernova_web.app import create_app


def _make_dist(tmp_path: Path) -> Path:
    """造一个最小前端 dist（结构对齐 vite build 产物）。"""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "index.js").write_text("// js bundle")
    (dist / "assets" / "index.css").write_text("body{}")
    (dist / "vite.svg").write_text("<svg></svg>")
    (dist / "index.html").write_text(
        '<html><head><title>Supernova</title></head>'
        '<body><div id="root"></div></body></html>'
    )
    return dist


def _client_with_dist(monkeypatch, tmp_path, brand=None):
    dist = _make_dist(tmp_path)
    monkeypatch.setenv("SUPERNOVA_WEB_FRONTEND_DIR", str(dist))
    # 隔离 workspaces 到 tmp（预建目录），防 set_brand_name / 真实 branding.json 污染盘。
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    (tmp_path / "workspaces").mkdir(exist_ok=True)
    # T11 后业务路由要求登录；测试走 HTTP，关掉 cookie Secure 标志才能登
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    if brand is not None:
        monkeypatch.setenv("SUPERNOVA_WEB_BRAND_NAME", brand)
    from supernova_web import config as cfg_mod
    cfg_mod.get_config.cache_clear()
    return TestClient(create_app())


def _login(c, username="tester", password="test-pw"):
    """T11 后业务 /api/* 要求登录；为 client 创建用户并登录，返回 csrf token（写操作用）。"""
    from supernova_web.auth.passwords import hash_password
    store = c.app.state.auth_store
    if store.get_user_by_username(username) is None:
        store.create_user(username, hash_password(password))
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": username, "password": password},
           headers={"X-CSRF-Token": tok})
    return tok


def test_serves_index_at_root(monkeypatch, tmp_path):
    client = _client_with_dist(monkeypatch, tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="root"' in r.text


def test_health_not_swallowed(monkeypatch, tmp_path):
    client = _client_with_dist(monkeypatch, tmp_path)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_api_routes_not_swallowed(monkeypatch, tmp_path):
    client = _client_with_dist(monkeypatch, tmp_path)
    # T11 后 /api/workspaces 要求登录；登录后该路由仍应返 JSON（不被 SPA fallback 吞掉）
    _login(client)
    r = client.get("/api/workspaces")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


def test_spa_fallback_deep_path(monkeypatch, tmp_path):
    client = _client_with_dist(monkeypatch, tmp_path)
    r = client.get("/workspaces/some-id")
    assert r.status_code == 200
    assert 'id="root"' in r.text


def test_assets_served(monkeypatch, tmp_path):
    client = _client_with_dist(monkeypatch, tmp_path)
    r = client.get("/assets/index.js")
    assert r.status_code == 200
    assert "js bundle" in r.text


def test_root_static_file_returned(monkeypatch, tmp_path):
    # dist 根目录的真实静态文件（如 vite.svg）应直返，而非当 SPA 路由
    client = _client_with_dist(monkeypatch, tmp_path)
    r = client.get("/vite.svg")
    assert r.status_code == 200
    assert "<svg" in r.text            # vite.svg 内容,非 index.html
    assert 'id="root"' not in r.text   # 不是 fallback 的 index.html


def test_no_frontend_dir_means_no_serving(monkeypatch):
    # dev 模式：不设 env → 后端不挂静态 → GET / 应 404（不崩）
    monkeypatch.delenv("SUPERNOVA_WEB_FRONTEND_DIR", raising=False)
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 404


def test_missing_dist_dir_does_not_crash(monkeypatch, tmp_path):
    # env 指向不存在目录 → create_app 不抛、GET / 返 404
    monkeypatch.setenv("SUPERNOVA_WEB_FRONTEND_DIR", str(tmp_path / "nonexistent"))
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 404


def test_path_traversal_blocked(monkeypatch, tmp_path):
    # dist 外放一个"敏感文件",确认遍历 payload 拿不到
    secret = tmp_path / "secrets.txt"
    secret.write_text("TOPSECRET")
    dist = _make_dist(tmp_path)   # dist 是 tmp_path/dist,secrets.txt 在 tmp_path 根(dist 之外)
    monkeypatch.setenv("SUPERNOVA_WEB_FRONTEND_DIR", str(dist))
    client = TestClient(create_app())
    for payload in ("/%2e%2e/secrets.txt", "/%2e%2e%2fsecrets.txt", "/..%2fsecrets.txt"):
        r = client.get(payload)
        assert r.status_code == 404, f"{payload} should be blocked"
        assert "TOPSECRET" not in r.text


def _client_with_brand(monkeypatch, tmp_path, brand):
    return _client_with_dist(monkeypatch, tmp_path, brand=brand)


def test_root_index_title_injects_brand(monkeypatch, tmp_path):
    # 后端注入：GET / 返回的 index.html <title> = 当前生效品牌名（刷新不再先显 Supernova 再跳）
    client = _client_with_brand(monkeypatch, tmp_path, "Acme Security")
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>Acme Security</title>" in r.text


def test_fallback_index_title_injects_brand(monkeypatch, tmp_path):
    # 深度路由回退到 index.html 时同样注入 title
    client = _client_with_brand(monkeypatch, tmp_path, "Acme")
    r = client.get("/workspaces/some-id")
    assert r.status_code == 200
    assert "<title>Acme</title>" in r.text


def test_index_title_defaults_to_supernova(monkeypatch, tmp_path):
    # 未设 brand → 回落默认 Supernova（与 index.html 模板一致，无跳变）
    monkeypatch.delenv("SUPERNOVA_WEB_BRAND_NAME", raising=False)
    client = _client_with_dist(monkeypatch, tmp_path)
    r = client.get("/")
    assert "<title>Supernova</title>" in r.text


def test_index_title_html_escaped(monkeypatch, tmp_path):
    # brand 含 HTML 特殊字符 → 必须转义，防破坏 <title> / 注入
    client = _client_with_brand(monkeypatch, tmp_path, "<b>X&Y</b>")
    r = client.get("/")
    assert "<title>&lt;b&gt;X&amp;Y&lt;/b&gt;</title>" in r.text
    assert "<title><b>" not in r.text


def test_static_file_not_title_injected(monkeypatch, tmp_path):
    # 真实静态文件（js/css/svg）走 FileResponse，不应被当 index.html 注入
    client = _client_with_brand(monkeypatch, tmp_path, "Acme")
    r = client.get("/assets/index.js")
    assert r.status_code == 200
    assert "js bundle" in r.text
    assert "<title>" not in r.text


def test_index_response_no_cache_header(monkeypatch, tmp_path):
    # SPA 入口 index.html 不应被浏览器/代理强缓存——否则改名后刷新仍显旧 title(命中 disk cache)
    client = _client_with_dist(monkeypatch, tmp_path)
    r = client.get("/")
    assert r.headers.get("cache-control", "").lower() == "no-cache"


def test_fallback_response_no_cache_header(monkeypatch, tmp_path):
    # 深度路由回退的 index.html 同样不应缓存
    client = _client_with_dist(monkeypatch, tmp_path)
    r = client.get("/workspaces/some-id")
    assert r.headers.get("cache-control", "").lower() == "no-cache"
