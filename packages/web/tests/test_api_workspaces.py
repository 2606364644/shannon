"""ws 级 API（list/create/delete 在 test_workspace_lifecycle/delete；本文件聚焦 list）。

旧 ws-scoped GET shim（/{ws}、/{ws}/deliverables|report|logs）已移除--详细场景迁移到
test_scans_api.py（scan-scoped /{ws}/scans/{scan_id}/...）。
"""
import json


def _ws(root, name, **kw):
    ws = root / name
    ws.mkdir(parents=True)
    data = {"status": "completed", "scan_type": "whitebox",
            "created_at": "2026-07-02T10:00:00Z", "completed_at": "2026-07-02T10:05:00Z"}
    data.update(kw)
    (ws / "session.json").write_text(json.dumps(data))


def test_list_workspaces(authed_client, tmp_workspaces):
    """GET /api/workspaces 列 ws（legacy ws 根 session.json 被 indexer 当 legacy scan 识别）。"""
    _ws(tmp_workspaces, "A")
    r = authed_client.get("/api/workspaces")
    assert r.status_code == 200
    assert any(w["name"] == "A" for w in r.json())


def test_removed_shim_get_workspace_now_405(authed_client, tmp_workspaces):
    """旧 GET /{ws} shim 已移除 -> 405（该 path 仅 DELETE /{ws} 留下，GET 方法不允许）。"""
    _ws(tmp_workspaces, "A")
    assert authed_client.get("/api/workspaces/A").status_code == 405


def test_removed_shim_report_now_404(authed_client, tmp_workspaces):
    """旧 GET /{ws}/report shim 已移除 -> 404。"""
    _ws(tmp_workspaces, "A")
    assert authed_client.get("/api/workspaces/A/report").status_code == 404
