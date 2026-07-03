from pathlib import Path

from fastapi.testclient import TestClient

from shannon_web.app import create_app


def _make_dist(tmp_path: Path) -> Path:
    """造一个最小前端 dist（结构对齐 vite build 产物）。"""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "index.js").write_text("// js bundle")
    (dist / "assets" / "index.css").write_text("body{}")
    (dist / "vite.svg").write_text("<svg></svg>")
    (dist / "index.html").write_text(
        '<html><body><div id="root"></div></body></html>'
    )
    return dist


def _client_with_dist(monkeypatch, tmp_path):
    dist = _make_dist(tmp_path)
    monkeypatch.setenv("SHANNON_WEB_FRONTEND_DIR", str(dist))
    return TestClient(create_app())


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


def test_no_frontend_dir_means_no_serving(monkeypatch):
    # dev 模式：不设 env → 后端不挂静态 → GET / 应 404（不崩）
    monkeypatch.delenv("SHANNON_WEB_FRONTEND_DIR", raising=False)
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 404


def test_missing_dist_dir_does_not_crash(monkeypatch, tmp_path):
    # env 指向不存在目录 → create_app 不抛、GET / 返 404
    monkeypatch.setenv("SHANNON_WEB_FRONTEND_DIR", str(tmp_path / "nonexistent"))
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 404
