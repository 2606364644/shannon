"""T6（spec 2026-08-26-report-generation-agent-design §7.1）：report-data API 测试。

GET /{ws}/scans/{scan_id}/report-data?track=whitebox|blackbox——读对应 deliverables 桶的
report_data.json，原文 JSON 返回；缺产物 404（前端回退 md 渲染路径）。
run 维度：GET .../blackbox-runs/{run_id}/report-data?track=combined 读 combined/run-K/
report_data.json；默认 track 读 run 黑盒桶。鉴权对齐 report 端点（workspace_member）。
"""
import json

import pytest

from supernova_core.utils.paths import combined_run_dir

REPORT_DATA = {
    "schema_version": 1,
    "scan": {"id": "s1", "track": "whitebox", "repo": "NodeGoat"},
    "executive_summary": {"narrative": "攻击面叙事", "risk_level": "极高",
                          "top_risks": [{"vuln_id": "XSS-VULN-01", "priority": "P0"}]},
    "stats": {"by_type": {"xss": {"count": 2}}, "by_severity": {"high": 2}},
    "vulnerabilities": [
        {"id": "XSS-VULN-01", "type": "xss", "severity": "high",
         "merge_source": "both", "merged_from": ["XSS-GN-13"],
         "endpoints": [{"method": "POST", "path": "/memos", "params": ["memo"]}],
         "poc": {"request": {"method": "POST", "url": "http://t/memos"}}},
    ],
}


def _make_scan(tmp_workspaces, ws, scan_id="s1", **extra):
    scan_dir = tmp_workspaces / ws / "scans" / scan_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    sess = {"status": "completed", "scan_type": "whitebox", "created_at": 1780000000.0,
            "web_url": "http://e", "repo_path": "/code", "owner": "web"}
    sess.update(extra)
    (scan_dir / "session.json").write_text(json.dumps(sess))
    return scan_dir


def _write_report_data(scan_dir, track="whitebox", payload=REPORT_DATA):
    d = scan_dir / "deliverables" / track
    d.mkdir(parents=True, exist_ok=True)
    (d / "report_data.json").write_text(json.dumps(payload, ensure_ascii=False))
    return d / "report_data.json"


# ── scan 级端点 ─────────────────────────────────────────────────────────────

def test_scan_report_data_whitebox(authed_client, tmp_workspaces):
    """?track=whitebox 读 deliverables/whitebox/report_data.json，原文 JSON 返回。"""
    scan_dir = _make_scan(tmp_workspaces, "WS")
    _write_report_data(scan_dir, "whitebox")
    r = authed_client.get("/api/workspaces/WS/scans/s1/report-data?track=whitebox")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["schema_version"] == 1
    assert body["scan"]["track"] == "whitebox"
    assert body["vulnerabilities"][0]["id"] == "XSS-VULN-01"


def test_scan_report_data_blackbox(authed_client, tmp_workspaces):
    """?track=blackbox 读黑盒桶（黑盒扫描的结构化报告）。"""
    scan_dir = _make_scan(tmp_workspaces, "WS", scan_type="blackbox")
    _write_report_data(scan_dir, "blackbox", {**REPORT_DATA,
                                              "scan": {"id": "s1", "track": "blackbox"}})
    r = authed_client.get("/api/workspaces/WS/scans/s1/report-data?track=blackbox")
    assert r.status_code == 200
    assert r.json()["scan"]["track"] == "blackbox"


def test_scan_report_data_auto_infer_blackbox(authed_client, tmp_workspaces):
    """无 track 参数 auto-infer（对齐 report 端点零回归语义）：纯黑盒扫描只有
    blackbox 桶 → 推断到 blackbox。"""
    scan_dir = _make_scan(tmp_workspaces, "WS", scan_type="blackbox")
    _write_report_data(scan_dir, "blackbox", {**REPORT_DATA,
                                              "scan": {"id": "s1", "track": "blackbox"}})
    r = authed_client.get("/api/workspaces/WS/scans/s1/report-data")
    assert r.status_code == 200
    assert r.json()["scan"]["track"] == "blackbox"


def test_scan_report_data_auto_infer_combined_falls_back_whitebox(authed_client, tmp_workspaces):
    """组合扫描 auto-infer 到 combined 桶（combined_report.md 存在）——但 combined 的
    report_data.json 是 per-run 产物（run 级端点服务），scan 级回落白盒桶。"""
    scan_dir = _make_scan(tmp_workspaces, "WS", combined=True)
    comb = scan_dir / "deliverables" / "combined" / "run-1"
    comb.mkdir(parents=True)
    (comb / "combined_report.md").write_text("# 融合")
    _write_report_data(scan_dir, "whitebox")
    r = authed_client.get("/api/workspaces/WS/scans/s1/report-data")
    assert r.status_code == 200
    assert r.json()["scan"]["track"] == "whitebox"


def test_scan_report_data_missing_404(authed_client, tmp_workspaces):
    """缺 report_data.json（旧 scan）→ 404（前端据此回退 md 渲染路径），非 500。"""
    _make_scan(tmp_workspaces, "WS")
    r = authed_client.get("/api/workspaces/WS/scans/s1/report-data?track=whitebox")
    assert r.status_code == 404


def test_scan_report_data_corrupt_json_404(authed_client, tmp_workspaces):
    """坏 JSON（写一半中断）→ 404 回退 md，不让 500 冒出。"""
    scan_dir = _make_scan(tmp_workspaces, "WS")
    d = scan_dir / "deliverables" / "whitebox"
    d.mkdir(parents=True)
    (d / "report_data.json").write_text("{truncated")
    assert authed_client.get(
        "/api/workspaces/WS/scans/s1/report-data?track=whitebox").status_code == 404


def test_scan_report_data_combined_track_422(authed_client, tmp_workspaces):
    """scan 级不支持 track=combined（融合产物 per-run，走 blackbox-runs 端点）→ 422。"""
    _make_scan(tmp_workspaces, "WS")
    r = authed_client.get("/api/workspaces/WS/scans/s1/report-data?track=combined")
    assert r.status_code == 422
    r2 = authed_client.get("/api/workspaces/WS/scans/s1/report-data?track=nonsense")
    assert r2.status_code == 422


def test_scan_report_data_unknown_scan_404(authed_client, tmp_workspaces):
    _make_scan(tmp_workspaces, "WS")
    assert authed_client.get(
        "/api/workspaces/WS/scans/nope/report-data?track=whitebox").status_code == 404


def test_scan_report_data_requires_auth(app_with_ws, tmp_workspaces):
    """鉴权对齐 report 端点（workspace_member）：未登录 → 401。"""
    from starlette.testclient import TestClient
    _make_scan(tmp_workspaces, "WS")
    c = TestClient(app_with_ws)
    assert c.get("/api/workspaces/WS/scans/s1/report-data?track=whitebox").status_code == 401


# ── run 级端点（combined / run 黑盒）────────────────────────────────────────

def _make_run(tmp_workspaces, ws, scan_id="s1", run_id="run-1"):
    from supernova_web.components.scan_store import ScanStore
    _make_scan(tmp_workspaces, ws, scan_id=scan_id)
    store = ScanStore(tmp_workspaces)
    store.create_blackbox_run(ws, scan_id)
    return tmp_workspaces / ws / "scans" / scan_id / "blackbox-runs" / run_id


def test_run_report_data_blackbox_default(authed_client, tmp_workspaces):
    """run 级默认 track：读 run 黑盒桶 deliverables/blackbox/report_data.json。"""
    run_dir = _make_run(tmp_workspaces, "WS")
    _write_report_data(run_dir, "blackbox", {**REPORT_DATA,
                                             "scan": {"id": "run-1", "track": "blackbox"}})
    r = authed_client.get("/api/workspaces/WS/scans/s1/blackbox-runs/run-1/report-data")
    assert r.status_code == 200
    assert r.json()["scan"]["track"] == "blackbox"
    r2 = authed_client.get(
        "/api/workspaces/WS/scans/s1/blackbox-runs/run-1/report-data?track=blackbox")
    assert r2.status_code == 200


def test_run_report_data_combined(authed_client, tmp_workspaces):
    """?track=combined 读 combined/run-K/report_data.json（融合报告 SSOT）。"""
    _make_run(tmp_workspaces, "WS")
    # combined 目录在任务根 deliverables/combined/run-1/（run 目录在 blackbox-runs/ 下）
    task_dir = tmp_workspaces / "WS" / "scans" / "s1"
    out = combined_run_dir(task_dir, "run-1")
    out.mkdir(parents=True)
    (out / "report_data.json").write_text(json.dumps(
        {**REPORT_DATA, "scan": {"id": "run-1", "track": "combined"}}, ensure_ascii=False))
    r = authed_client.get(
        "/api/workspaces/WS/scans/s1/blackbox-runs/run-1/report-data?track=combined")
    assert r.status_code == 200
    assert r.json()["scan"]["track"] == "combined"


def test_run_report_data_missing_404(authed_client, tmp_workspaces):
    """run 存在但无 report_data.json → 404。"""
    _make_run(tmp_workspaces, "WS")
    assert authed_client.get(
        "/api/workspaces/WS/scans/s1/blackbox-runs/run-1/report-data").status_code == 404
    assert authed_client.get(
        "/api/workspaces/WS/scans/s1/blackbox-runs/run-1/report-data?track=combined"
    ).status_code == 404


def test_run_report_data_unknown_run_404(authed_client, tmp_workspaces):
    """run 不存在（combined 亦然——先验 run 存在性，防 run_id 越界路径）→ 404。"""
    _make_scan(tmp_workspaces, "WS")
    assert authed_client.get(
        "/api/workspaces/WS/scans/s1/blackbox-runs/run-9/report-data").status_code == 404
    assert authed_client.get(
        "/api/workspaces/WS/scans/s1/blackbox-runs/run-9/report-data?track=combined"
    ).status_code == 404


def test_run_report_data_invalid_track_422(authed_client, tmp_workspaces):
    _make_run(tmp_workspaces, "WS")
    assert authed_client.get(
        "/api/workspaces/WS/scans/s1/blackbox-runs/run-1/report-data?track=whitebox"
    ).status_code == 422
