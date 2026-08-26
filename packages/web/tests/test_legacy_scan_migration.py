# packages/web/tests/test_legacy_scan_migration.py
# 守护（2026-08-27）：启动收纳已退役——web 启动（lifespan 全序列）不得移动/拆除
# workspaces 根下的任何目录，也不得创建 __legacy__ 工作区。历史：_migrate_legacy_scans
# 曾在每次启动把 ws 根平铺 scan（情况 A）与伪 ws（情况 B）收纳进 __legacy__/scans/，
# 为 CLI 直连模式的去污染机制。决策：部署已全走 web UI（scan 落 workspaces/<ws>/scans/），
# CLI 直连实质废弃，收纳机制连同 __legacy__ 概念一并退役；手动放置的目录一律保持原样。
#
# 注意路径：cfg.workspaces_dir = resolve_workspaces_dir() = SUPERNOVA_WORKER_ROOT/"workspaces"。
import json

from starlette.testclient import TestClient

from supernova_web.app import create_app


def _boot(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_web.config import get_config
    get_config.cache_clear()
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir(parents=True, exist_ok=True)
    return ws_root


def test_startup_leaves_root_scan_untouched(tmp_path, monkeypatch):
    """lifespan 后 ws 根平铺 scan（CLI 直连形态）原样留在原地，不收纳进 __legacy__。"""
    ws_root = _boot(tmp_path, monkeypatch)
    scan = ws_root / "whitebox-1756234567"
    scan.mkdir()
    (scan / "session.json").write_text(json.dumps({
        "status": "completed", "created_at": 1780000000.0,
    }), encoding="utf-8")
    (scan / "events.ndjson").write_text("", encoding="utf-8")

    with TestClient(create_app()):  # 触发 lifespan 全启动序列
        pass

    assert (scan / "session.json").exists(), (
        f"root scan was relocated during startup: {scan}"
    )
    assert not (ws_root / "__legacy__").exists(), "startup created __legacy__ workspace"


def test_startup_leaves_pseudo_ws_untouched(tmp_path, monkeypatch):
    """lifespan 后已固化伪 ws（auto owner + scan 命名）原样保留，不拆进 __legacy__。"""
    from supernova_web.components.scan_store import write_workspace_meta

    ws_root = _boot(tmp_path, monkeypatch)
    pseudo = ws_root / "myrepo-20260722-180616"
    (pseudo / "scans" / "20260722-180616").mkdir(parents=True)
    write_workspace_meta(pseudo, name=pseudo.name, owner="web")

    with TestClient(create_app()):
        pass

    assert (pseudo / "workspace.json").exists(), (
        f"pseudo ws was dismantled during startup: {pseudo}"
    )
    assert (pseudo / "scans" / "20260722-180616").is_dir()
    assert not (ws_root / "__legacy__").exists(), "startup created __legacy__ workspace"
