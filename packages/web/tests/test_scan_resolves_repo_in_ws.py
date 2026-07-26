# packages/web/tests/test_scan_resolves_repo_in_ws.py
import pytest
from supernova_web.components.scan_manager import ScanManager


def test_resolve_repo_path_uses_ws(tmp_path):
    ws_dir = tmp_path / "workspaces"; ws_dir.mkdir()
    (ws_dir / "ws1" / "repos" / "myrepo").mkdir(parents=True)
    (ws_dir / "ws1" / "repos" / "myrepo" / ".git").mkdir()
    sm = ScanManager(ws_dir, tmp_path / "repos", None)
    p = sm._resolve_repo_path("ws1", "myrepo")
    assert p.endswith("ws1/repos/myrepo")


def test_resolve_repo_path_ws_isolation(tmp_path):
    ws_dir = tmp_path / "workspaces"; ws_dir.mkdir()
    (ws_dir / "ws1" / "repos" / "r").mkdir(parents=True)
    sm = ScanManager(ws_dir, tmp_path / "repos", None)
    import pytest
    with pytest.raises(ValueError):
        sm._resolve_repo_path("ws2", "r")  # ws2 没这个 repo
