import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_no_roots(app_with_ws):
    """默认 fs_roots=[]（整机可见）。"""
    return app_with_ws


@pytest.fixture
def app_with_roots(tmp_path, monkeypatch):
    """配 SUPERNOVA_FS_ROOTS=tmp_path 限制可见根。"""
    monkeypatch.setenv("SUPERNOVA_FS_ROOTS", str(tmp_path))
    # T11 auth cascade: 同 app_with_ws 的 remount（workspaces_dir 解析到 tmp_path/workspaces）
    from supernova_core.utils.paths import resolve_workspaces_dir
    (tmp_path / "workspaces").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_web import config as cfg_mod
    cfg_mod.get_config.cache_clear()
    from supernova_web.app import create_app
    app = create_app()
    cfg_mod.get_config.cache_clear()
    return app


def _login(c, app):
    """T11 后 /api/fs/browse 要求登录；直接生成 session 注入 sn-sid cookie。

    app_with_ws fixture 不关 cookie_secure，走真实 /login 会被 Secure 标志卡；
    这里绕过登录链路直接造 session（装配补全，无业务断言改动）。GET 不需 CSRF。
    """
    from supernova_web.auth.passwords import hash_password
    store = app.state.auth_store
    if store.get_user_by_username("tester") is None:
        store.create_user("tester", hash_password("test-pw"))
    sid = app.state.session_manager.create(store.get_user_by_username("tester").id)
    c.cookies.set("sn-sid", sid)
    return c


def test_list_dir_entries(app_no_roots, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / ".hidden").write_text("y")
    client = _login(TestClient(app_no_roots), app_no_roots)
    r = client.get("/api/fs/browse", params={"path": str(tmp_path)})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == str(tmp_path.resolve())
    names = {e["name"]: e for e in body["entries"]}
    assert names["sub"]["type"] == "dir"
    assert names["a.txt"]["type"] == "file"
    assert names[".hidden"]["type"] == "file"  # dotfiles 显示
    assert "size" in names["a.txt"]


def test_parent_root_is_null(app_no_roots, tmp_path):
    client = _login(TestClient(app_no_roots), app_no_roots)
    # tmp_path 的 parent 有值；根目录（/）的 parent 为 null
    r = client.get("/api/fs/browse", params={"path": str(tmp_path)})
    assert r.json()["parent"] == str(tmp_path.resolve().parent)
    root = client.get("/api/fs/browse", params={"path": "/"})
    assert root.json()["parent"] is None


def test_sort_dirs_first(app_no_roots, tmp_path):
    # 用 tmp_path 子目录隔离（app_with_ws 经 tmp_workspaces 在 tmp_path 下建 workspaces/）
    d = tmp_path / "data"
    d.mkdir()
    (d / "z_dir").mkdir()
    (d / "a_file").write_text("x")
    (d / "m_dir").mkdir()
    client = _login(TestClient(app_no_roots), app_no_roots)
    entries = client.get("/api/fs/browse", params={"path": str(d)}).json()["entries"]
    types = [e["type"] for e in entries]
    # 目录全在文件前
    assert types == ["dir", "dir", "file"]


def test_reject_relative_path(app_no_roots):
    client = _login(TestClient(app_no_roots), app_no_roots)
    r = client.get("/api/fs/browse", params={"path": "relative/path"})
    assert r.status_code == 400


def test_traversal_rejected(app_no_roots, tmp_path):
    client = _login(TestClient(app_no_roots), app_no_roots)
    # / 等安全路径；用相对 .. 测 400（is_absolute false）
    r = client.get("/api/fs/browse", params={"path": "../../etc"})
    assert r.status_code == 400


def test_not_exist_404(app_no_roots, tmp_path):
    client = _login(TestClient(app_no_roots), app_no_roots)
    r = client.get("/api/fs/browse", params={"path": str(tmp_path / "nope")})
    assert r.status_code == 404


def test_file_not_dir_400(app_no_roots, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    client = _login(TestClient(app_no_roots), app_no_roots)
    r = client.get("/api/fs/browse", params={"path": str(f)})
    assert r.status_code == 400


def test_allowlist_violation_409(app_with_roots, tmp_path):
    # roots=[tmp_path]；访问 tmp_path 之外 → 409
    client = _login(TestClient(app_with_roots), app_with_roots)
    r = client.get("/api/fs/browse", params={"path": str(tmp_path.parent)})
    assert r.status_code == 409


def test_allowlist_inside_ok(app_with_roots, tmp_path):
    (tmp_path / "sub").mkdir()
    client = _login(TestClient(app_with_roots), app_with_roots)
    r = client.get("/api/fs/browse", params={"path": str(tmp_path / "sub")})
    assert r.status_code == 200


def test_tilde_expands_home(app_no_roots):
    client = _login(TestClient(app_no_roots), app_no_roots)
    r = client.get("/api/fs/browse", params={"path": "~"})
    assert r.status_code == 200
    assert r.json()["path"] == os.path.expanduser("~")


def test_truncated(monkeypatch, app_no_roots, tmp_path):
    # 造 10 个 entry，MAX_ENTRIES 改 5 → truncated=True + 5 条
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x")
    from supernova_web.api import fs as fs_mod
    monkeypatch.setattr(fs_mod, "MAX_ENTRIES", 5)
    client = _login(TestClient(app_no_roots), app_no_roots)
    body = client.get("/api/fs/browse", params={"path": str(tmp_path)}).json()
    assert body["truncated"] is True
    assert len(body["entries"]) == 5
