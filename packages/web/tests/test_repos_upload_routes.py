"""上传 zip 端点（POST /repos/upload）路由测试：multipart、权限、413/409、
upload 仓 pull/checkout/branches 405。"""
import io
import zipfile

import pytest
from starlette.testclient import TestClient
from supernova_web.app import create_app
from supernova_web.auth.passwords import hash_password


@pytest.fixture
def _app(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    app = create_app()
    st = app.state.auth_store
    st.create_user("admin", hash_password("p"), role="admin")
    st.create_user("alice", hash_password("p"))
    (app.state.config.workspaces_dir / "ws1").mkdir()
    st.add_workspace_member("ws1", st.get_user_by_username("alice").id, "manager")
    return app


def _login(app, username):
    c = TestClient(app)
    tok = c.get("/api/auth/csrf").json()["csrf_token"]
    c.post("/api/auth/login", json={"username": username, "password": "p"},
           headers={"X-CSRF-Token": tok})
    return c


def _csrf(c):
    return c.get("/api/auth/csrf").json()["csrf_token"]


def _zip_buf(files: dict[str, str] | None = None) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in (files or {"a.py": "x = 1\n"}).items():
            zf.writestr(path, content)
    buf.seek(0)
    return buf


def test_member_upload_accepted(_app):
    """成员上传无 .git zip → 202 + name + 可见骨架（extracting meta）。

    后台解压/快照化到 ready 的完整路径由组件级测试覆盖（test_repo_upload.py）——
    TestClient 每请求独立 event loop，create_task 的后台 task 不会跨请求推进
    （clone 路由测试同样只断言 202，既有策略）。
    """
    alice = _login(_app, "alice")
    r = alice.post("/api/workspaces/ws1/repos/upload",
                   files={"file": ("app.zip", _zip_buf(), "application/zip")},
                   headers={"X-CSRF-Token": _csrf(alice)})
    assert r.status_code == 202
    assert r.json() == {"name": "app"}
    view = alice.get("/api/workspaces/ws1/repos/app").json()
    assert view["source"]["kind"] == "upload"
    assert view["state"] in ("extracting", "stale", "failed")  # 骨架已落（TestClient 下 task 不推进）
    repos_root = _app.state.config.workspaces_dir / "ws1" / "repos"
    assert (repos_root / "app" / ".supernova-repo.json").exists()


def test_upload_requires_membership(_app):
    bob = _login(_app, "bob") if _app.state.auth_store.get_user_by_username("bob") else None
    if bob is None:  # bob 未建则建（对齐 _app 只建 alice 的现状）
        _app.state.auth_store.create_user("bob", hash_password("p"))
        bob = _login(_app, "bob")
    r = bob.post("/api/workspaces/ws1/repos/upload",
                 files={"file": ("app.zip", _zip_buf(), "application/zip")},
                 headers={"X-CSRF-Token": _csrf(bob)})
    assert r.status_code == 403


def test_upload_too_large_413(_app, monkeypatch):
    from supernova_web.config import get_config
    monkeypatch.setattr(get_config(), "max_upload_zip_bytes", 10)
    alice = _login(_app, "alice")
    r = alice.post("/api/workspaces/ws1/repos/upload",
                   files={"file": ("app.zip", _zip_buf({"big.txt": "a" * 500}), "application/zip")},
                   headers={"X-CSRF-Token": _csrf(alice)})
    assert r.status_code == 413


def test_upload_name_conflict_409(_app):
    alice = _login(_app, "alice")
    (_app.state.config.workspaces_dir / "ws1" / "repos" / "app").mkdir(parents=True)
    r = alice.post("/api/workspaces/ws1/repos/upload",
                   files={"file": ("app.zip", _zip_buf(), "application/zip")},
                   headers={"X-CSRF-Token": _csrf(alice)})
    assert r.status_code == 409


def test_upload_non_zip_409(_app):
    alice = _login(_app, "alice")
    r = alice.post("/api/workspaces/ws1/repos/upload",
                   files={"file": ("app.tar.gz", _zip_buf(), "application/zip")},
                   headers={"X-CSRF-Token": _csrf(alice)})
    assert r.status_code == 409


def test_upload_custom_name_and_group(_app):
    alice = _login(_app, "alice")
    r = alice.post("/api/workspaces/ws1/repos/upload",
                   files={"file": ("app.zip", _zip_buf(), "application/zip")},
                   data={"name": "custom", "group": "g"},
                   headers={"X-CSRF-Token": _csrf(alice)})
    assert r.status_code == 202 and r.json() == {"name": "g/custom"}


def test_upload_repo_git_endpoints_405(_app):
    """upload 仓是静态快照：pull/checkout/branches 一律 405（对齐 linked 仓）。

    直接手写 ready 的 upload meta（405 挡板只依赖 meta.source.kind；后台 task 推进
    与否不影响本断言——TestClient 下不推进，见 test_member_upload_accepted 注）。
    """
    import json as _json
    repo = _app.state.config.workspaces_dir / "ws1" / "repos" / "app"
    (repo / ".git").mkdir(parents=True)
    (repo / ".supernova-repo.json").write_text(_json.dumps({
        "name": "app", "state": "ready", "source": {"kind": "upload", "branch": "main"}}))
    alice = _login(_app, "alice")
    tok = {"X-CSRF-Token": _csrf(alice)}
    assert alice.post("/api/workspaces/ws1/repos/app/pull", headers=tok).status_code == 405
    assert alice.post("/api/workspaces/ws1/repos/app/checkout",
                      json={"branch": "main"}, headers=tok).status_code == 405
    assert alice.get("/api/workspaces/ws1/repos/app/branches").status_code == 405
