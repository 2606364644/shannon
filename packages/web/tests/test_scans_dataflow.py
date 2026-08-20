"""P5: dataflow 端点 200 / 404 / tier fallback。

GET /api/workspaces/{ws}/scans/{scan_id}/dataflow 读
deliverables/whitebox/intermediate/dataflow_view.json（tier fallback：先
intermediate/ 再桶平铺），返 200 JSON / 404 {"detail":"dataflow view not generated"}。

fixture 惯例对齐 tests/test_scans_api.py：复用 conftest 的 authed_client +
tmp_workspaces，直接在 tmp_workspaces 下建 scan 目录写 session.json（不经
scan_manager.start，免 temporal）。
"""
import json


def _make_scan(tmp_workspaces, ws, scan_id="s1"):
    """直接在 tmp_workspaces 建 scan（对齐 test_scans_api._make_scan 惯例）。"""
    scan_dir = tmp_workspaces / ws / "scans" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    sess = {"status": "completed", "scan_type": "whitebox", "created_at": 1780000000.0,
            "web_url": "http://e", "repo_path": "/code", "owner": "web"}
    (scan_dir / "session.json").write_text(json.dumps(sess))
    return scan_dir


def _dataflow_payload():
    """最小合法 dataflow_view.json（schema_version=1，对齐 core dataflow_view.py）。"""
    return {"schema_version": 1, "flows": [], "nodes": [], "edges": []}


def test_dataflow_200(authed_client, tmp_workspaces):
    """intermediate/ 下有 dataflow_view.json -> 200 + 透传 schema_version。"""
    scan_dir = _make_scan(tmp_workspaces, "w1")
    inter = scan_dir / "deliverables" / "whitebox" / "intermediate"
    inter.mkdir(parents=True)
    (inter / "dataflow_view.json").write_text(json.dumps(_dataflow_payload()))
    r = authed_client.get("/api/workspaces/w1/scans/s1/dataflow")
    assert r.status_code == 200
    assert r.json()["schema_version"] == 1


def test_dataflow_404_when_missing(authed_client, tmp_workspaces):
    """scan 存在但无 dataflow_view.json（intermediate/ 与平铺都无）-> 404。"""
    _make_scan(tmp_workspaces, "w1")
    r = authed_client.get("/api/workspaces/w1/scans/s1/dataflow")
    assert r.status_code == 404
    assert "not generated" in r.json()["detail"].lower()


def test_dataflow_tier_fallback_flat(authed_client, tmp_workspaces):
    """旧扫描平铺：deliverables/whitebox/dataflow_view.json（无 intermediate/）-> 200。

    resolve_intermediate tier fallback：intermediate/ 无则回退桶平铺。
    """
    scan_dir = _make_scan(tmp_workspaces, "w1")
    wb = scan_dir / "deliverables" / "whitebox"
    wb.mkdir(parents=True)
    (wb / "dataflow_view.json").write_text(json.dumps(_dataflow_payload()))
    r = authed_client.get("/api/workspaces/w1/scans/s1/dataflow")
    assert r.status_code == 200
    assert r.json()["schema_version"] == 1


def test_dataflow_404_when_scan_missing(authed_client, tmp_workspaces):
    """scan 不存在 -> 404（scan not found，先于 dataflow not generated）。"""
    r = authed_client.get("/api/workspaces/w1/scans/nope/dataflow")
    assert r.status_code == 404
