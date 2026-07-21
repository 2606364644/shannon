import json
import pytest
from fastapi.testclient import TestClient
from supernova_web.app import create_app


def _app(tmp_path, monkeypatch, repos):
    monkeypatch.setenv("SUPERNOVA_REPOS_DIR", str(tmp_path / "repos"))
    (tmp_path / "repos").mkdir(parents=True, exist_ok=True)
    for name, state in repos.items():
        d = tmp_path / "repos" / name; d.mkdir()
        (d / ".supernova-repo.json").write_text(json.dumps({"name": name, "state": state}))
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    from supernova_web.config import get_config; get_config.cache_clear()
    return create_app()


def test_list_repos(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"foo": "ready", "bar": "failed"})
    r = TestClient(app).get("/api/repos")
    assert r.status_code == 200
    names = [x["name"] for x in r.json()]
    assert names == ["bar", "foo"]
    states = {x["name"]: x["state"] for x in r.json()}
    assert states["foo"] == "ready" and states["bar"] == "failed"


def test_get_repo_detail(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, {"foo": "ready"})
    r = TestClient(app).get("/api/repos/foo")
    assert r.status_code == 200 and r.json()["name"] == "foo"
    assert TestClient(app).get("/api/repos/missing").status_code == 404


def test_post_repo_503_no_creds(tmp_path, monkeypatch):
    monkeypatch.delenv("GITLAB_USER", raising=False)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    app = _app(tmp_path, monkeypatch, {})
    r = TestClient(app).post("/api/repos", json={"git_url": "https://x/foo.git"})
    assert r.status_code == 503


def test_post_repo_409_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("GITLAB_USER", "u"); monkeypatch.setenv("GITLAB_TOKEN", "t")
    app = _app(tmp_path, monkeypatch, {"foo": "ready"})
    r = TestClient(app).post("/api/repos", json={"git_url": "https://x/foo.git"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_delete_busy_409(tmp_path, monkeypatch):
    # 注：brief 原版为 sync + asyncio.create_task —— 3.12 无 running loop 会 RuntimeError。
    # 改 async（与 test_sse_events 同模式）即可让 asyncio.create_task 取得 loop。
    import asyncio
    import httpx
    app = _app(tmp_path, monkeypatch, {"foo": "cloning"})
    app.state.repo_manager._jobs["foo"] = asyncio.create_task(asyncio.sleep(10))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.delete("/api/repos/foo")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_sse_events(tmp_path, monkeypatch):
    import httpx
    app = _app(tmp_path, monkeypatch, {"foo": "ready"})
    (tmp_path / "repos" / "foo" / "clone.ndjson").write_text(
        json.dumps({"ts": "t1", "phase": "cloning", "progress": 40, "status": "progress"}) + "\n"
        + json.dumps({"ts": "t2", "type": "clone_end", "status": "ready"}) + "\n")
    transport = httpx.ASGITransport(app=app)
    lines = []
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=5) as c:
        async with c.stream("GET", "/api/repos/foo/events") as r:
            assert r.status_code == 200
            async for line in r.aiter_lines():
                lines.append(line)
                if line.startswith("data:") and "clone_end" in line:
                    break
    assert any("40" in l for l in lines if l.startswith("data:"))


def test_get_repo_grouped_path(tmp_path, monkeypatch):
    """{name:path} 吃 group/repo 含 '/' 的路径，返回 group 字段。"""
    app = _app(tmp_path, monkeypatch, {})
    base = tmp_path / "repos"
    d = base / "frontend" / "foo"; d.mkdir(parents=True)
    (d / ".supernova-repo.json").write_text(json.dumps({"name": "frontend/foo", "state": "ready"}))
    r = TestClient(app).get("/api/repos/frontend/foo")
    assert r.status_code == 200
    assert r.json()["name"] == "frontend/foo"
    assert r.json()["group"] == "frontend"
    # 分组目录本身 404
    assert TestClient(app).get("/api/repos/frontend").status_code == 404
