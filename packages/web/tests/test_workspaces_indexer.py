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
