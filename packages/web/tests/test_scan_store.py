"""T1: ScanStore — 1 ws : N scans 存储层。

create_scan 落 workspaces/<ws>/scans/<scan_id>/session.json（复用 core
SessionManager(scans_dir) 的 create_workspace）；list_scans 双源合并（scans/<id>/
新 scan + ws 根 legacy session.json），按 created_at 倒序；get_scan_dir 路径校验
拒越界（..///）；latest_scan 取最新。

core SessionManager 的读写方法只收 workspace_path，故 SessionManager(ws_dir/"scans")
把 scans 目录当 workspaces 根即可复用全部 scan 读写——core 零改动（CLAUDE.md §1
铁律不碰，core session.py 不改）。
"""
import json
from datetime import datetime

import pytest

from supernova_web.components.scan_store import ScanStore, ScanSummary


def _make_legacy_root_scan(ws_dir, created_at=1780000000.0, status="completed",
                           scan_type="whitebox"):
    """在 ws 根写 legacy session.json（模拟 CLI/worker.py 旧路径产出的 scan）。"""
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "session.json").write_text(json.dumps({
        "status": status, "scan_type": scan_type, "created_at": created_at,
        "web_url": "", "repo_path": "",
    }))


# ── create_scan ────────────────────────────────────────────────────────────

def test_create_scan_lands_in_scans_subdir(tmp_path):
    store = ScanStore(tmp_path)
    scan_id, scan_dir = store.create_scan("WS1", "http://e", "/code/x")
    assert scan_dir == tmp_path / "WS1" / "scans" / scan_id
    assert (scan_dir / "session.json").exists()
    sess = json.loads((scan_dir / "session.json").read_text())
    assert sess["web_url"] == "http://e"
    assert sess["repo_path"] == "/code/x"
    assert sess["status"] == "running"
    assert sess["scan_type"] == "whitebox"


def test_create_scan_id_format(tmp_path):
    """scan_id = <repo>-YYYYMMDD-HHMMSS（仓库名前缀 + 本地时区紧凑秒级）。"""
    store = ScanStore(tmp_path)
    scan_id, _ = store.create_scan("WS", "u", "/code/NodeGoat")
    # 形如 NodeGoat-20260729-171759
    assert scan_id.startswith("NodeGoat-")
    ts = scan_id[len("NodeGoat-"):]
    assert len(ts) == 15 and ts[8] == "-"  # YYYYMMDD-HHMMSS


def test_create_scan_same_second_collision(monkeypatch, tmp_path):
    """同秒同名碰撞 -> 第二个追加 -2（_gen_scan_id 序号）。"""
    import supernova_web.components.scan_store as mod
    fixed = datetime(2026, 7, 27, 14, 30, 0)
    monkeypatch.setattr(mod, "_now_local", lambda: fixed)
    store = ScanStore(tmp_path)
    id1, _ = store.create_scan("WS", "u", "/x")
    id2, _ = store.create_scan("WS", "u", "/x")
    assert id1 == "x-20260727-143000"
    assert id2 == "x-20260727-143000-2"
    # 第三个 -3
    id3, _ = store.create_scan("WS", "u", "/x")
    assert id3 == "x-20260727-143000-3"


def test_create_scan_repo_name_prefix(tmp_path):
    """scan_id 以仓库名（repo_path basename）为前缀；空 repo_path fallback 'repo'。"""
    store = ScanStore(tmp_path)
    sid_repo, _ = store.create_scan("WS", "u", "/code/NodeGoat")
    assert sid_repo.startswith("NodeGoat-")
    sid_empty, _ = store.create_scan("WS", "u", "")
    assert sid_empty.startswith("repo-")


# ── list_scans（双源）────────────────────────────────────────────────────────

def test_list_scans_new_scan_only(tmp_path):
    store = ScanStore(tmp_path)
    store.create_scan("WS1", "http://e", "/x")
    scans = store.list_scans("WS1")
    assert len(scans) == 1
    assert isinstance(scans[0], ScanSummary)
    assert scans[0].scan_type == "whitebox"


def test_list_scans_empty_ws(tmp_path):
    """无 scan 的 ws（仅 workspace.json/repos，或新建空 ws）-> 空列表。"""
    store = ScanStore(tmp_path)
    (tmp_path / "WS").mkdir()
    assert store.list_scans("WS") == []
    assert store.list_scans("NOPE") == []


def test_list_scans_dual_source_new_plus_legacy(tmp_path):
    """双源：scans/<id>/ 新 scan + ws 根 legacy session.json，合并按 created_at 倒序。"""
    store = ScanStore(tmp_path)
    # legacy 根 scan（旧 created_at）
    _make_legacy_root_scan(tmp_path / "WS1", created_at=1780000000.0)
    # 新 scan（更新 created_at）
    new_id, _ = store.create_scan("WS1", "http://e", "/x")
    scans = store.list_scans("WS1")
    assert len(scans) == 2
    # 新 scan created_at > legacy -> 排第一
    assert scans[0].scan_id == new_id
    # legacy 排第二
    assert scans[1].scan_id != new_id
    assert scans[1].created_at == 1780000000.0


def test_list_scans_multiple_new_scans_sorted_desc(tmp_path):
    """同 ws 多个新 scan，按 created_at 倒序。"""
    store = ScanStore(tmp_path)
    # 建三个 scan（同秒 scan_id 碰撞 -2/-3），再各自覆盖 created_at 拉开时间。
    createds = [1780000000.0, 1780003600.0, 1780001800.0]  # 10:00 / 11:00 / 11:30 派生
    dirs = []
    for _ in range(3):
        _, d = store.create_scan("WS", "u", "/x")
        dirs.append(d)
    for d, c in zip(dirs, createds):
        sess = json.loads((d / "session.json").read_text())
        sess["created_at"] = c
        (d / "session.json").write_text(json.dumps(sess))
    scans = store.list_scans("WS")
    assert len(scans) == 3
    # createds[1](11:00 派生) > createds[2] > createds[0]
    assert scans[0].created_at == createds[1]
    assert scans[1].created_at == createds[2]
    assert scans[2].created_at == createds[0]


# ── get_scan_dir（路径校验 + 双源定位）────────────────────────────────────────

def test_get_scan_dir_new_scan(tmp_path):
    store = ScanStore(tmp_path)
    new_id, new_dir = store.create_scan("WS", "u", "/x")
    assert store.get_scan_dir("WS", new_id) == new_dir


def test_get_scan_dir_legacy_root(tmp_path):
    """legacy ws 根 scan：get_scan_dir 据派生 legacy_id 定位回 ws 根。"""
    store = ScanStore(tmp_path)
    _make_legacy_root_scan(tmp_path / "WS2", created_at=1780000000.0)
    legacy_id = store._legacy_scan_id(tmp_path / "WS2")
    assert store.get_scan_dir("WS2", legacy_id) == tmp_path / "WS2"


def test_get_scan_dir_unknown_returns_none(tmp_path):
    store = ScanStore(tmp_path)
    store.create_scan("WS", "u", "/x")
    assert store.get_scan_dir("WS", "nonexistent") is None


def test_get_scan_dir_rejects_traversal(tmp_path):
    r"""路径校验：scan_id 含 .. / 正斜杠 / 反斜杠 -> None（防越界读其他目录）。"""
    store = ScanStore(tmp_path)
    store.create_scan("WS", "u", "/x")
    assert store.get_scan_dir("WS", "..") is None
    assert store.get_scan_dir("WS", "../..") is None
    assert store.get_scan_dir("WS", "foo/bar") is None
    assert store.get_scan_dir("WS", "foo\\bar") is None
    assert store.get_scan_dir("WS", "") is None


# ── latest_scan ────────────────────────────────────────────────────────────

def test_latest_scan_returns_newest(tmp_path):
    store = ScanStore(tmp_path)
    _make_legacy_root_scan(tmp_path / "WS", created_at=1780000000.0)
    new_id, new_dir = store.create_scan("WS", "u", "/x")
    assert store.latest_scan("WS") == new_dir


def test_latest_scan_prefers_running_over_newer_completed(tmp_path):
    """active 优先：更新的已完成 scan + 较旧的在跑 scan -> 返回在跑的那个。

    shim DELETE /api/scan/{ws} cancel latest/active 依赖此：同 ws 多 scan 时 cancel
    正在跑的，而非更新的已完成 scan（spec §5.2 latest_scan active 优先）。
    """
    import time
    store = ScanStore(tmp_path)
    # 较旧的 scan，标 running + fresh heartbeat（在跑）
    _, running_dir = store.create_scan("WS", "u", "/x")
    sess = json.loads((running_dir / "session.json").read_text())
    sess["created_at"] = 1780000000.0  # 较旧
    sess["status"] = "running"
    (running_dir / "session.json").write_text(json.dumps(sess))
    (running_dir / "heartbeat").write_text(f"{time.time()}\n")  # fresh -> running
    # 更新的 scan，标 completed（不在跑）
    _, done_dir = store.create_scan("WS", "u", "/x")
    sess2 = json.loads((done_dir / "session.json").read_text())
    sess2["created_at"] = 1780003600.0  # 更新
    sess2["status"] = "completed"
    (done_dir / "session.json").write_text(json.dumps(sess2))
    # active 优先 -> 返回 running_dir（虽更旧），而非 done_dir（更新但已完成）
    assert store.latest_scan("WS") == running_dir


def test_latest_scan_legacy_only(tmp_path):
    """只有 legacy 根 scan -> latest_scan 返回 ws 根。"""
    store = ScanStore(tmp_path)
    _make_legacy_root_scan(tmp_path / "WS", created_at=1780000000.0)
    assert store.latest_scan("WS") == tmp_path / "WS"


def test_latest_scan_empty_returns_none(tmp_path):
    store = ScanStore(tmp_path)
    assert store.latest_scan("NOPE") is None


# ── ScanSummary 字段 ─────────────────────────────────────────────────────────

def test_scan_summary_fields_cost_vuln_running(tmp_path):
    """ScanSummary 聚合 vuln_count/total_cost_usd/cost_currency/is_running。"""
    store = ScanStore(tmp_path)
    new_id, scan_dir = store.create_scan("WS", "u", "/x")
    # 写 metrics + 漏洞 queue + 标 completed
    (scan_dir / "deliverables" / "whitebox").mkdir(parents=True)
    (scan_dir / "deliverables" / "whitebox" / "xss_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": [{}, {}]}))
    sess = json.loads((scan_dir / "session.json").read_text())
    sess["metrics"] = {"total_cost_usd": 1.5, "cost_currency": "CNY"}
    sess["status"] = "completed"
    (scan_dir / "session.json").write_text(json.dumps(sess))
    s = store.list_scans("WS")[0]
    assert s.vuln_count == 2
    assert s.total_cost_usd == 1.5
    assert s.cost_currency == "CNY"
    assert s.status == "completed"
    assert s.is_running is False
    assert isinstance(s.created_at, float)


def test_scan_summary_running_when_heartbeat_fresh(tmp_path):
    """新 scan 无终态 + heartbeat fresh -> status=running, is_running=True。"""
    import time
    store = ScanStore(tmp_path)
    _, scan_dir = store.create_scan("WS", "u", "/x")
    (scan_dir / "heartbeat").write_text(f"{time.time()}\n")
    s = store.list_scans("WS")[0]
    assert s.status == "running"
    assert s.is_running is True


# ── workflow_id（前端任务名展示）──────────────────────────────────────────────

def test_scan_summary_workflow_id(tmp_path):
    """workflow_id = {ws}-{scan_id}[-resume-N]（读 resumeAttempts 算 N）。"""
    store = ScanStore(tmp_path)
    new_id, scan_dir = store.create_scan("WS1", "u", "/x")
    s = store.list_scans("WS1")[0]
    # 首次（无 resumeAttempts）-> {ws}-{scan_id}
    assert s.workflow_id == f"WS1-{new_id}"
    assert s.as_dict()["workflow_id"] == f"WS1-{new_id}"
    # 写 resumeAttempts（模拟已 resume 1 次）-> 加 -resume-1
    sess = json.loads((scan_dir / "session.json").read_text())
    sess["resumeAttempts"] = [{"at": 1}]
    (scan_dir / "session.json").write_text(json.dumps(sess))
    s2 = store.list_scans("WS1")[0]
    assert s2.workflow_id == f"WS1-{new_id}-resume-1"


def test_scan_summary_workflow_id_legacy(tmp_path):
    """legacy ws 根 scan 的 workflow_id = {ws}-{legacy_id}（读 ws 根 session.json）。"""
    store = ScanStore(tmp_path)
    _make_legacy_root_scan(tmp_path / "WS2", created_at=1780000000.0)
    s = store.list_scans("WS2")[0]
    legacy_id = store._legacy_scan_id(tmp_path / "WS2")
    assert s.workflow_id == f"WS2-{legacy_id}"


def test_scan_summary_workflow_id_prefers_ndjson_header(tmp_path):
    """events.ndjson 首行 WorkflowHeader.workflow_id 是真实 temporal id（single source of
    truth）——CLI/legacy scan 的 workflow_id = workspace_name（CLI scheme），与 web scan 的
    {ws}-{scan_id}（web scheme）不同，算不出来只能读。有 ndjson 时优先读，覆盖算的值。

    真机 sentinel_dashboard scan 即此结构：scan_id=20260721-121435（migration 派生），
    但 ndjson WorkflowHeader.workflow_id=sentinel_dashboard_20260721-201435（CLI workspace_name）。
    """
    store = ScanStore(tmp_path)
    new_id, scan_dir = store.create_scan("WS1", "u", "/x")
    (scan_dir / "events.ndjson").write_text(
        json.dumps({"ts": "2026-07-21 20:14:35", "category": "HEADER",
                    "type": "WorkflowHeader",
                    "workflow_id": "sentinel_dashboard_20260721-201435"}) + "\n"
    )
    s = store.list_scans("WS1")[0]
    assert s.workflow_id == "sentinel_dashboard_20260721-201435"
    assert s.workflow_id != f"WS1-{new_id}"  # 不是算的 web scheme
