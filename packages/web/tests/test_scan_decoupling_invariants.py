"""T6: 1 ws : N scans 解耦不变量断言（spec §10 铁律边界）。"""
import json

import supernova_core.session as core_session
from pathlib import Path


# ── 不变量 4: 一个 scan_id 仅对应一个 session.json（无 ws 根泄漏）─────────────

def test_one_scan_id_one_session_json(tmp_path):
    """新 scan: 仅 scans/<id>/session.json，ws 根不泄漏 session.json。"""
    from supernova_web.components.scan_store import ScanStore
    store = ScanStore(tmp_path)
    scan_id, scan_dir = store.create_scan("WS", "http://e", "/x")
    assert (scan_dir / "session.json").exists()
    assert not (tmp_path / "WS" / "session.json").exists()  # ws 根无泄漏
    # 该 ws 仅此一个 session.json（scans/<id>/ 内）
    session_jsons = list((tmp_path / "WS").rglob("session.json"))
    assert len(session_jsons) == 1


def test_migrate_no_root_session_leak(tmp_path):
    """迁移后 legacy ws 根 session.json 入 scans/<id>/，ws 根无残留 session.json。"""
    from supernova_web.app import _migrate_legacy_scans
    from types import SimpleNamespace
    ws = tmp_path / "WS"
    ws.mkdir()
    (ws / "session.json").write_text(json.dumps(
        {"status": "completed", "created_at": 1780000000.0, "owner": "web"}))
    app = SimpleNamespace(state=SimpleNamespace(
        config=SimpleNamespace(workspaces_dir=tmp_path)))
    _migrate_legacy_scans(app)
    # ws 根无 session.json（搬入 scans/<id>/），但仍可见（workspace.json）
    assert not (ws / "session.json").exists()
    assert (ws / "workspace.json").exists()
    session_jsons = list(ws.rglob("session.json"))
    assert len(session_jsons) == 1  # 仅 scans/<id>/session.json


# ── 不变量 2: GET /api/workspaces ws status = latest scan 聚合 ─────────────────

def test_ws_status_equals_latest_scan(authed_client, tmp_workspaces):
    """ws 行 status = latest scan 的 status（不混入 scan-only 字段作 ws 状态）。"""
    from supernova_web.components.scan_store import ScanStore
    from supernova_web.components.scan_store import write_workspace_meta
    ws_dir = tmp_workspaces / "WS"
    ws_dir.mkdir()
    write_workspace_meta(ws_dir, name="WS", owner="admin")
    store = ScanStore(tmp_workspaces)
    # 旧 scan completed，新 scan failed（更新）-> latest=failed
    _, d1 = store.create_scan("WS", "http://e", "/x")
    s1 = json.loads((d1 / "session.json").read_text())
    s1["status"] = "completed"; s1["created_at"] = 1780000000.0
    (d1 / "session.json").write_text(json.dumps(s1))
    _, d2 = store.create_scan("WS", "http://e", "/x")
    s2 = json.loads((d2 / "session.json").read_text())
    s2["status"] = "failed"; s2["created_at"] = 1780003600.0
    (d2 / "session.json").write_text(json.dumps(s2))

    rows = authed_client.get("/api/workspaces").json()
    row = next(r for r in rows if r["name"] == "WS")
    assert row["status"] == "failed"            # = latest scan status
    assert row["latest_status"] == "failed"
    assert row["scan_count"] == 2


# ── 不变量 1: core SessionManager 源码零改动（关键签名未变）──────────────────

def test_core_session_manager_signatures_unchanged():
    """core SessionManager 源码零改动（CLAUDE.md §1 铁律 + spec §8.1/§10.1）。

    grep 关键方法签名未变 -- web 复用 SessionManager(scans_dir)，不改正它。
    """
    src = Path(core_session.__file__).read_text("utf-8")
    # 关键方法签名（web 依赖的复用面）
    assert "def create_workspace(self, web_url: str, repo_path: str, name: str | None = None, *, scan_type: str = \"whitebox\") -> Path:" in src
    assert "def list_workspaces(self) -> list[Path]:" in src
    assert "def get_session_data(self, workspace_path: Path) -> dict:" in src
    assert "def update_session(self, workspace_path: Path, data: dict) -> None:" in src
    assert "def get_status(self, workspace_path: Path) -> str:" in src
    assert "def get_created_at(self, workspace_path: Path) -> float | None:" in src
    # 读写方法只收 workspace_path（不依赖 workspaces_dir 做读写）-- 复用基石
    assert "self.workspaces_dir" in src  # 仅 create/list/delete 用 workspaces_dir
