import json
import shutil

from fastapi.testclient import TestClient


def _make_ws(root, name, status="completed"):
    ws = root / name
    ws.mkdir(parents=True)
    (ws / "session.json").write_text(json.dumps({
        "status": status, "scan_type": "whitebox",
        "created_at": "2026-07-02T10:00:00Z",
    }))


def test_delete_completed_ws(app_with_ws, tmp_workspaces):
    _make_ws(tmp_workspaces, "done-ws")
    client = TestClient(app_with_ws)
    r = client.delete("/api/workspaces/done-ws")
    assert r.status_code == 200
    assert r.json() == {"deleted": "done-ws"}
    assert not (tmp_workspaces / "done-ws").exists()
    # list 不再返
    assert all(w["name"] != "done-ws" for w in client.get("/api/workspaces").json())


def test_delete_not_exist_404(app_with_ws):
    client = TestClient(app_with_ws)
    assert client.delete("/api/workspaces/nope").status_code == 404


def test_delete_running_rejected_409(app_with_ws, tmp_workspaces):
    _make_ws(tmp_workspaces, "running-ws", status="running")
    # 注入一个 alive pid（当前进程）使 indexer 判运行中
    import os
    app_with_ws.state.indexer.set_active_pid("running-ws", os.getpid())
    client = TestClient(app_with_ws)
    r = client.delete("/api/workspaces/running-ws")
    assert r.status_code == 409
    assert (tmp_workspaces / "running-ws").exists()  # 未删
