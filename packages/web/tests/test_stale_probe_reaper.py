"""stale probe reaper:启动期清 workspaces/<ws>/auth-probes/*/ 残留目录(worker 异常残留)。"""
from pathlib import Path
from unittest.mock import MagicMock
from supernova_web.components.scan_manager import ScanManager


def test_reap_stale_probes_removes_orphan_probe_dirs(tmp_path):
    ws = tmp_path / "ws1"
    probe = ws / "auth-probes" / "probe-stale"
    probe.mkdir(parents=True)
    (probe / "scan-config.yaml").write_text("authentication: {}", "utf-8")
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock())
    mgr.reap_stale_probes()
    assert not probe.exists()
    assert (ws / "auth-probes").exists() is False  # 空目录一并清


def test_reap_stale_probes_no_dir_is_noop(tmp_path):
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock())
    mgr.reap_stale_probes()  # 无 auth-probes,不报错
