"""T5: legacy scan 迁移（启动幂等）测试。

ws 根 legacy session.json（1:1 混存时代的 scan 状态机）-> scans/<legacy_id>/；
ws 级（workspace.json/config.yaml/repos/）留根；幂等 + best-effort + 不阻断启动。
"""
import json
from datetime import datetime
from types import SimpleNamespace

from supernova_web.app import _migrate_legacy_scans


def _app(workspaces_dir):
    """最小 app mock：仅 _migrate_legacy_scans 需要的 app.state.config.workspaces_dir。"""
    return SimpleNamespace(state=SimpleNamespace(
        config=SimpleNamespace(workspaces_dir=workspaces_dir)))


def _legacy_id(created_at):
    return datetime.fromtimestamp(created_at).strftime("%Y%m%d-%H%M%S")


def test_migrate_legacy_root_scan(tmp_path):
    """ws 根 session.json + 产物 -> scans/<legacy_id>/；config.yaml 留根；补 workspace.json。"""
    ws = tmp_path / "WS1"
    ws.mkdir()
    (ws / "session.json").write_text(json.dumps({
        "status": "completed", "scan_type": "whitebox", "created_at": 1780000000.0,
        "web_url": "http://e", "repo_path": "/code", "owner": "web"}))
    (ws / "events.ndjson").write_text('{"type":"scan_end"}\n')
    (ws / "deliverables").mkdir()
    (ws / "config.yaml").write_text("ai: x")  # ws 级配置 -> 留根

    _migrate_legacy_scans(_app(tmp_path))

    assert not (ws / "session.json").exists()  # 已搬走
    scans = list((ws / "scans").iterdir())
    assert len(scans) == 1
    legacy_dir = scans[0]
    assert (legacy_dir / "session.json").exists()
    assert (legacy_dir / "events.ndjson").exists()
    assert (legacy_dir / "deliverables").exists()
    # ws 级保留
    assert (ws / "config.yaml").exists()
    # 补 workspace.json
    meta = json.loads((ws / "workspace.json").read_text())
    assert meta["owner"] == "web"
    assert meta["name"] == "WS1"


def test_migrate_idempotent(tmp_path):
    """再跑不重复迁移（ws 根 session.json 已搬走 -> 第二次跳过）。"""
    ws = tmp_path / "WS"
    ws.mkdir()
    (ws / "session.json").write_text(json.dumps(
        {"status": "completed", "created_at": 1780000000.0, "owner": "web"}))

    _migrate_legacy_scans(_app(tmp_path))
    assert len(list((ws / "scans").iterdir())) == 1
    _migrate_legacy_scans(_app(tmp_path))  # 再跑
    assert len(list((ws / "scans").iterdir())) == 1  # 无重复


def test_migrate_corrupted_session_skips(tmp_path):
    """损坏 session.json -> 跳过不崩，session.json 留根。"""
    ws = tmp_path / "WS"
    ws.mkdir()
    (ws / "session.json").write_text("not json{")
    _migrate_legacy_scans(_app(tmp_path))
    assert (ws / "session.json").exists()  # 未搬
    assert not (ws / "scans").exists() or not list((ws / "scans").iterdir())


def test_migrate_multiple_ws(tmp_path):
    """多 ws 并存各自迁移。"""
    for name in ("A", "B"):
        w = tmp_path / name
        w.mkdir()
        (w / "session.json").write_text(json.dumps(
            {"status": "completed", "created_at": 1780000000.0}))
    _migrate_legacy_scans(_app(tmp_path))
    for name in ("A", "B"):
        assert len(list((tmp_path / name / "scans").iterdir())) == 1


def test_migrate_skips_new_model_ws(tmp_path):
    """新模型 ws（workspace.json，无根 session.json）-> 跳过（不创建空 scans/）。"""
    from supernova_web.components.scan_store import write_workspace_meta
    ws = tmp_path / "NEW"
    ws.mkdir()
    write_workspace_meta(ws, name="NEW", owner="admin")
    _migrate_legacy_scans(_app(tmp_path))
    assert not (ws / "scans").exists() or not list((ws / "scans").iterdir())


def test_migrate_collision_appends_suffix(tmp_path):
    """scans/<legacy_id>/ 已存在（新 scan 占用同 id）-> 碰撞 -2。"""
    ws = tmp_path / "WS"
    ws.mkdir()
    (ws / "session.json").write_text(json.dumps(
        {"status": "completed", "created_at": 1780000000.0}))
    legacy_id = _legacy_id(1780000000.0)
    # 预占 scans/<legacy_id>/（模拟已有同 id 新 scan）
    (ws / "scans" / legacy_id).mkdir(parents=True)
    (ws / "scans" / legacy_id / "session.json").write_text("{}")

    _migrate_legacy_scans(_app(tmp_path))

    assert (ws / "scans" / f"{legacy_id}-2" / "session.json").exists()


def test_migrate_empty_workspaces_dir(tmp_path):
    """空 workspaces 目录 -> 不崩（no-op）。"""
    _migrate_legacy_scans(_app(tmp_path))  # 无 ws
    # 不抛即过
    assert True


def test_migrate_legacy_id_from_created_at(tmp_path):
    """legacy_id 从 created_at 派生 YYYYMMDD-HHMMSS。"""
    ws = tmp_path / "WS"
    ws.mkdir()
    (ws / "session.json").write_text(json.dumps(
        {"status": "completed", "created_at": 1780000000.0}))
    _migrate_legacy_scans(_app(tmp_path))
    expected = _legacy_id(1780000000.0)
    assert (ws / "scans" / expected / "session.json").exists()
