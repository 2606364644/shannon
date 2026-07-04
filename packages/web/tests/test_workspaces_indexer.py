import json
import os

import pytest

from shannon_web.components.workspaces_indexer import WorkspacesIndexer


def _make_ws(root, name, status="completed", scan_type="whitebox", queues=None, nested=False):
    ws = root / name
    ws.mkdir(parents=True)
    data = {"status": status, "scan_type": scan_type,
            "created_at": "2026-07-02T10:00:00Z", "completed_at": "2026-07-02T10:05:00Z"}
    payload = {"session": data} if nested else data
    (ws / "session.json").write_text(json.dumps(payload))
    if queues:
        dl = ws / "deliverables" / "whitebox"
        dl.mkdir(parents=True)
        for cls, n in queues.items():
            (dl / f"{cls}_exploitation_queue.json").write_text(
                json.dumps({"vulnerabilities": [{}] * n}))


def test_completed_with_vuln_counts(tmp_workspaces):
    _make_ws(tmp_workspaces, "NodeGoat_x", status="completed", queues={"xss": 3, "ssrf": 1})
    rows = WorkspacesIndexer(tmp_workspaces).list_workspaces()
    assert len(rows) == 1
    assert rows[0]["name"] == "NodeGoat_x"
    assert rows[0]["status"] == "completed"
    assert rows[0]["vuln_counts"] == {"xss": 3, "ssrf": 1}


def test_nested_legacy_session_format(tmp_workspaces):
    _make_ws(tmp_workspaces, "Old_y", status="failed", scan_type="whitebox", nested=True)
    rows = WorkspacesIndexer(tmp_workspaces).list_workspaces()
    assert rows[0]["status"] == "failed"
    assert rows[0]["scan_type"] == "whitebox"


def test_running_when_pid_alive(tmp_workspaces):
    _make_ws(tmp_workspaces, "Run_z", status=None)
    idx = WorkspacesIndexer(tmp_workspaces)
    idx.set_active_pid("Run_z", os.getpid())
    assert idx.list_workspaces()[0]["status"] == "running"


def test_interrupted_when_no_pid_no_status(tmp_workspaces):
    _make_ws(tmp_workspaces, "Dead_w", status=None)
    idx = WorkspacesIndexer(tmp_workspaces)
    assert idx.list_workspaces()[0]["status"] == "interrupted"


def test_correlation_marked(tmp_workspaces):
    _make_ws(tmp_workspaces, "Cor_c", status="completed", scan_type="correlation")
    rows = WorkspacesIndexer(tmp_workspaces).list_workspaces()
    assert rows[0]["is_correlation"] is True


def test_sorts_by_created_at_desc(tmp_workspaces):
    _make_ws(tmp_workspaces, "A", )
    _make_ws(tmp_workspaces, "B")
    # B 的新 session 已写；用覆盖法给 A 更早
    (tmp_workspaces / "A" / "session.json").write_text(json.dumps(
        {"status": "completed", "scan_type": "whitebox",
         "created_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:05:00Z"}))
    names = [r["name"] for r in WorkspacesIndexer(tmp_workspaces).list_workspaces()]
    assert names[0] == "B"


def test_list_supplements_cost_duration_links_vuln_count(tmp_workspaces):
    """list_workspaces 补返 total_cost_usd/total_duration_ms/vuln_count(number)/links。"""
    import json
    ws = tmp_workspaces / "full-ws"
    ws.mkdir()
    (ws / "session.json").write_text(json.dumps({
        "status": "completed", "scan_type": "whitebox",
        "created_at": 1780000000.0,
        "metrics": {"total_cost_usd": 1.23, "total_duration_ms": 45000},
        "links": {"child_workspaces": ["child-a", "child-b"]},
    }))
    from shannon_web.components.workspaces_indexer import WorkspacesIndexer
    rows = WorkspacesIndexer(tmp_workspaces).list_workspaces()
    row = next(r for r in rows if r["name"] == "full-ws")
    assert row["total_cost_usd"] == 1.23
    assert row["total_duration_ms"] == 45000
    assert row["links"] == {"child_workspaces": ["child-a", "child-b"]}
    # vuln_count 是聚合后的 number（无漏洞数据 → 0）
    assert row["vuln_count"] == 0
    assert isinstance(row["vuln_count"], int)


def test_list_vuln_count_aggregates_dict(tmp_workspaces):
    """vuln_counts dict → vuln_count number（sum values）。"""
    import json
    ws = tmp_workspaces / "agg-ws"
    ws.mkdir()
    (ws / "session.json").write_text(json.dumps({
        "status": "completed", "scan_type": "whitebox", "created_at": 1,
    }))
    from shannon_web.components.workspaces_indexer import WorkspacesIndexer
    idx = WorkspacesIndexer(tmp_workspaces)
    # mock get_workspace_vuln_counts 返多类型 dict
    import shannon_web.components.workspaces_indexer as mod
    orig = mod.get_workspace_vuln_counts
    mod.get_workspace_vuln_counts = lambda _p: {"injection": 3, "xss": 2}
    try:
        rows = idx.list_workspaces()
    finally:
        mod.get_workspace_vuln_counts = orig
    row = next(r for r in rows if r["name"] == "agg-ws")
    assert row["vuln_count"] == 5
    assert row["vuln_counts"] == {"injection": 3, "xss": 2}


def test_list_missing_metrics_returns_none(tmp_workspaces):
    """session.json 无 metrics → total_cost_usd/duration 为 None，不崩。"""
    import json
    ws = tmp_workspaces / "bare-ws"
    ws.mkdir()
    (ws / "session.json").write_text(json.dumps({
        "status": "completed", "scan_type": "whitebox", "created_at": 1,
    }))
    from shannon_web.components.workspaces_indexer import WorkspacesIndexer
    row = next(r for r in WorkspacesIndexer(tmp_workspaces).list_workspaces() if r["name"] == "bare-ws")
    assert row["total_cost_usd"] is None
    assert row["total_duration_ms"] is None
    assert row["links"] == {}
