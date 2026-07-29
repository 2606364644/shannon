"""legacy scan 迁移（启动幂等）测试。

workspaces 根下的旧 scan 统一收纳进 ``__legacy__`` ws 的 ``scans/`` 下：
- 情况 A：未固化 legacy scan（根 session.json，无 workspace.json/config.yaml）
  -> 搬进 ``__legacy__/scans/<legacy_id>/``，原目录删除，不再提升为 ws。
- 情况 B：已固化伪 ws（workspace.json 且 owner 为自动值 {legacy,host,web}
  且目录名匹配 scan 命名）-> 备份 workspace.json，scans 搬进 ``__legacy__/scans/``，
  删原目录。
- 真 ws / __legacy__ 自身 / 无 session.json 残留 -> 不动。

幂等 + best-effort + 不阻断启动。
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


def _write_session(path, **extra):
    payload = {"status": "completed", "created_at": 1780000000.0, "owner": "web"}
    payload.update(extra)
    path.write_text(json.dumps(payload))


def _write_pseudo_ws(root, name, owner="web", scan_subdir="20260722-180616"):
    """造一个已固化伪 ws（workspace.json + scans/<id>/session.json + deliverables）。"""
    from supernova_web.components.scan_store import write_workspace_meta
    ws = root / name
    ws.mkdir(parents=True)
    write_workspace_meta(ws, name=name, owner=owner)
    scan = ws / "scans" / scan_subdir
    scan.mkdir(parents=True)
    _write_session(scan / "session.json")
    (scan / "deliverables").mkdir()
    return ws


# --- 情况 A：未固化 legacy scan（根 session.json）-> __legacy__/scans/ ---

def test_legacy_root_scan_moved_to_legacy_ws(tmp_path):
    """根 session.json scan -> __legacy__/scans/<legacy_id>/；原目录删除。"""
    ws = tmp_path / "NodeGoat_20260713-231325"
    ws.mkdir()
    _write_session(ws / "session.json", scan_type="whitebox")
    (ws / "events.ndjson").write_text('{"type":"scan_end"}\n')
    (ws / "deliverables").mkdir()

    _migrate_legacy_scans(_app(tmp_path))

    assert not ws.exists()  # 原目录整体搬走删除
    legacy_scans = tmp_path / "__legacy__" / "scans"
    scans = list(legacy_scans.iterdir())
    assert len(scans) == 1
    assert (scans[0] / "session.json").exists()
    assert (scans[0] / "events.ndjson").exists()
    assert (scans[0] / "deliverables").exists()


def test_legacy_root_scan_not_promoted_to_ws(tmp_path):
    """根 session.json scan 不再被提升为独立 ws（不写 workspace.json）。"""
    ws = tmp_path / "NodeGoat_20260713-231325"
    ws.mkdir()
    _write_session(ws / "session.json")
    _migrate_legacy_scans(_app(tmp_path))
    assert not ws.exists()  # 不提升：原目录删除，无 workspace.json 落地


def test_ensures_legacy_ws_meta(tmp_path):
    """迁移后 __legacy__ 有 workspace.json（使 indexer read_workspace_meta 可见）。"""
    ws = tmp_path / "NodeGoat_20260713-231325"
    ws.mkdir()
    _write_session(ws / "session.json")
    _migrate_legacy_scans(_app(tmp_path))
    meta = json.loads((tmp_path / "__legacy__" / "workspace.json").read_text())
    assert meta["name"] == "__legacy__"


def test_corrupted_session_skips(tmp_path):
    """损坏 session.json -> 跳过不崩，session.json 留根。"""
    ws = tmp_path / "NodeGoat_20260713-231325"
    ws.mkdir()
    (ws / "session.json").write_text("not json{")
    _migrate_legacy_scans(_app(tmp_path))
    assert (ws / "session.json").exists()  # 未搬
    legacy_scans = tmp_path / "__legacy__" / "scans"
    assert not legacy_scans.exists() or not list(legacy_scans.iterdir())


def test_multiple_scans_all_to_legacy(tmp_path):
    """多 scan 并存，全部进 __legacy__/scans/，原目录都删。"""
    for name in ("NodeGoat_20260713-231325", "localhost_shannon-1780589669779"):
        w = tmp_path / name
        w.mkdir()
        _write_session(w / "session.json")
    _migrate_legacy_scans(_app(tmp_path))
    assert len(list((tmp_path / "__legacy__" / "scans").iterdir())) == 2
    assert not (tmp_path / "NodeGoat_20260713-231325").exists()


def test_collision_appends_suffix(tmp_path):
    """__legacy__/scans/<legacy_id>/ 已占用 -> 碰撞 -2。"""
    ws = tmp_path / "NodeGoat_20260713-231325"
    ws.mkdir()
    _write_session(ws / "session.json", created_at=1780000000.0)
    legacy_id = _legacy_id(1780000000.0)
    # 预占 __legacy__/scans/<legacy_id>/（模拟已有同 id scan）
    pre = tmp_path / "__legacy__" / "scans" / legacy_id
    pre.mkdir(parents=True)
    (pre / "session.json").write_text("{}")

    _migrate_legacy_scans(_app(tmp_path))

    assert (tmp_path / "__legacy__" / "scans" / f"{legacy_id}-2" / "session.json").exists()


def test_legacy_id_from_created_at(tmp_path):
    """legacy_id 从 created_at 派生 YYYYMMDD-HHMMSS，落在 __legacy__/scans/。"""
    ws = tmp_path / "NodeGoat_20260713-231325"
    ws.mkdir()
    _write_session(ws / "session.json", created_at=1780000000.0)
    _migrate_legacy_scans(_app(tmp_path))
    expected = _legacy_id(1780000000.0)
    assert (tmp_path / "__legacy__" / "scans" / expected / "session.json").exists()


def test_empty_workspaces_dir(tmp_path):
    """空 workspaces 目录 -> 不崩（no-op）。"""
    _migrate_legacy_scans(_app(tmp_path))
    assert True


def test_ws_with_config_yaml_not_moved(tmp_path):
    """根 session.json + config.yaml（ws 级配置）-> 当 ws 跳过，不搬进 __legacy__。"""
    ws = tmp_path / "NodeGoat_20260713-231325"
    ws.mkdir()
    _write_session(ws / "session.json")
    (ws / "config.yaml").write_text("ai: x")
    _migrate_legacy_scans(_app(tmp_path))
    assert (ws / "session.json").exists()  # 未搬
    assert (ws / "config.yaml").exists()


def test_residue_without_session_not_touched(tmp_path):
    """仅 deliverables、无 session.json 的半截残留 -> 不动。"""
    ws = tmp_path / "NodeGoat_1784214772"
    ws.mkdir()
    (ws / "deliverables").mkdir()
    _migrate_legacy_scans(_app(tmp_path))
    assert ws.exists()
    assert (ws / "deliverables").exists()


# --- 情况 B：已固化伪 ws -> 降级进 __legacy__ ---

def test_downgrade_pseudo_ws(tmp_path):
    """已固化伪 ws（owner=web + scan 命名）-> scans 搬进 __legacy__，原目录删。"""
    ws = _write_pseudo_ws(tmp_path, "NodeGoat_1784743576", owner="web")
    _migrate_legacy_scans(_app(tmp_path))
    assert not ws.exists()
    assert len(list((tmp_path / "__legacy__" / "scans").iterdir())) == 1


def test_downgrade_backs_up_workspace_json(tmp_path):
    """降级前备份 workspace.json -> __legacy__/.migrated/<name>.json。"""
    _write_pseudo_ws(tmp_path, "NodeGoat_1784743576", owner="web")
    _migrate_legacy_scans(_app(tmp_path))
    backup = tmp_path / "__legacy__" / ".migrated" / "NodeGoat_1784743576.json"
    assert backup.exists()
    data = json.loads(backup.read_text())
    assert data["owner"] == "web"


def test_pseudo_ws_scan_name_variants_downgrade(tmp_path):
    """各种 scan 命名（纯epoch / -epoch / shannon-epoch / YYYYMMDD-HHMMSS）均降级。"""
    names = [
        "NodeGoat_1784743576",
        "juice-shop_whitebox-1780587584138",
        "localhost_shannon-1780589669779",
        "NodeGoat_20260713-231325",
    ]
    for i, name in enumerate(names):
        _write_pseudo_ws(tmp_path, name, owner="web", scan_subdir=f"scan-{i}")
    _migrate_legacy_scans(_app(tmp_path))
    assert len(list((tmp_path / "__legacy__" / "scans").iterdir())) == 4
    for name in names:
        assert not (tmp_path / name).exists()


def test_real_ws_not_downgraded(tmp_path):
    """workspace.json owner=admin（真实用户）-> 不降级，scans 留原 ws。"""
    ws = _write_pseudo_ws(tmp_path, "NodeGoat_1784743576", owner="admin")
    _migrate_legacy_scans(_app(tmp_path))
    assert ws.exists()
    assert (ws / "scans").exists()  # scans 未搬走


def test_non_scan_named_ws_not_downgraded(tmp_path):
    """owner=web 但目录名非 scan 命名 -> 不降级（防误删真 ws）。"""
    ws = _write_pseudo_ws(tmp_path, "myproject", owner="web")
    _migrate_legacy_scans(_app(tmp_path))
    assert ws.exists()


def test_legacy_ws_itself_excluded(tmp_path):
    """__legacy__ 自身不被处理。"""
    from supernova_web.components.scan_store import write_workspace_meta
    legacy = tmp_path / "__legacy__"
    legacy.mkdir()
    write_workspace_meta(legacy, name="__legacy__", owner="legacy")
    (legacy / "repos").mkdir()  # 模拟 P2 迁入的 legacy repo
    _migrate_legacy_scans(_app(tmp_path))
    assert (legacy / "repos").exists()  # 未动
    assert (legacy / "workspace.json").exists()


# --- 幂等 ---

def test_idempotent_legacy_scan(tmp_path):
    """再跑不重复迁移（A 搬完根无 session.json -> 第二次跳过）。"""
    ws = tmp_path / "NodeGoat_20260713-231325"
    ws.mkdir()
    _write_session(ws / "session.json")
    _migrate_legacy_scans(_app(tmp_path))
    n1 = len(list((tmp_path / "__legacy__" / "scans").iterdir()))
    _migrate_legacy_scans(_app(tmp_path))
    n2 = len(list((tmp_path / "__legacy__" / "scans").iterdir()))
    assert n1 == n2 == 1


def test_idempotent_downgrade(tmp_path):
    """伪 ws 降级后，再跑不重复（原目录已删）。"""
    _write_pseudo_ws(tmp_path, "NodeGoat_1784743576", owner="web")
    _migrate_legacy_scans(_app(tmp_path))
    n1 = len(list((tmp_path / "__legacy__" / "scans").iterdir()))
    _migrate_legacy_scans(_app(tmp_path))
    n2 = len(list((tmp_path / "__legacy__" / "scans").iterdir()))
    assert n1 == n2 == 1
