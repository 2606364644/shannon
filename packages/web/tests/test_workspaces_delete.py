import json

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
    """活跃判定改用 _status_of==running:heartbeat fresh(非 pid)→ running → 409。"""
    import time
    _make_ws(tmp_workspaces, "running-ws", status="running")
    (tmp_workspaces / "running-ws" / "heartbeat").write_text(f"{time.time()}\n")  # fresh → running
    client = TestClient(app_with_ws)
    r = client.delete("/api/workspaces/running-ws")
    assert r.status_code == 409
    assert (tmp_workspaces / "running-ws").exists()  # 未删


def test_delete_after_cancel_allowed(app_with_ws, tmp_workspaces):
    """cancel 标 cancelled(终态优先于 heartbeat)后 _status_of≠running → 立即可删(spec §4.7)。"""
    import time
    _make_ws(tmp_workspaces, "cancelled-ws", status="cancelled")
    # heartbeat 仍 fresh,但终态优先 → cancelled(非 running)→ 可删
    (tmp_workspaces / "cancelled-ws" / "heartbeat").write_text(f"{time.time()}\n")
    client = TestClient(app_with_ws)
    r = client.delete("/api/workspaces/cancelled-ws")
    assert r.status_code == 200
    assert not (tmp_workspaces / "cancelled-ws").exists()
