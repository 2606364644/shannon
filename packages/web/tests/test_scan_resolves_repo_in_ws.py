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


def test_resolve_repo_path_linked(tmp_path):
    """关联仓库源：命中 linked_repos.json → 返回其存储路径（不走 <ws>/repos）。"""
    from supernova_web.components.repo_manager import write_linked_repos
    ws_dir = tmp_path / "workspaces"; ws_dir.mkdir()
    target = tmp_path / "external" / "repo"; target.mkdir(parents=True)
    write_linked_repos(ws_dir / "ws1",
                       [{"name": "linked-repo", "path": str(target), "linked_at": "x"}])
    sm = ScanManager(ws_dir, tmp_path / "repos", None)
    assert sm._resolve_repo_path("ws1", "linked-repo") == str(target)


def test_resolve_repo_path_linked_no_state_check(tmp_path):
    """关联仓库无 .git / 无 .supernova-repo.json 也能扫（关联无 clone 状态，不卡 ready）。"""
    from supernova_web.components.repo_manager import write_linked_repos
    ws_dir = tmp_path / "workspaces"; ws_dir.mkdir()
    target = tmp_path / "plain"; target.mkdir()  # 无 .git 无 meta
    write_linked_repos(ws_dir / "ws1",
                       [{"name": "data", "path": str(target), "linked_at": "x"}])
    sm = ScanManager(ws_dir, tmp_path / "repos", None)
    assert sm._resolve_repo_path("ws1", "data") == str(target)
