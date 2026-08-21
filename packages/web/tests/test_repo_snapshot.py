"""repo-snapshot.json：提交时快照仓库 branch/commit 进 scan_dir，ScanSummary 读回。

spec：docs/superpowers/specs/2026-08-21-repo-branch-switch-design.md §4。
切分支后同一仓扫不同分支，报告靠此快照区分来源（scan_id 只含仓库名+时间戳）。
"""
import json
from pathlib import Path

from supernova_web.components.scan_store import ScanStore

WS = "ws1"


def _repo_with_meta(tmp_path, branch="main", commit="abc123def") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".supernova-repo.json").write_text(json.dumps({
        "name": "repo", "state": "ready",
        "source": {"kind": "git", "url": "https://x/repo.git",
                   "branch": branch, "commit": commit}}))
    return repo


def _linked_repo_with_head(tmp_path, branch="dev") -> Path:
    repo = tmp_path / "linked"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
    return repo


# ---- write/read helpers（scan_store）----

def test_write_repo_snapshot_from_meta(tmp_path):
    """私有克隆：读 .supernova-repo.json 的 source.branch/commit 写快照。"""
    from supernova_web.components.scan_store import REPO_SNAPSHOT_FILE, write_repo_snapshot
    scan_dir = tmp_path / "scan"; scan_dir.mkdir()
    write_repo_snapshot(scan_dir, str(_repo_with_meta(tmp_path)))
    data = json.loads((scan_dir / REPO_SNAPSHOT_FILE).read_text())
    assert data == {"branch": "main", "commit": "abc123def"}


def test_write_repo_snapshot_linked_reads_git_head(tmp_path):
    """linked 仓（无 meta）：读 .git/HEAD 的 ref 解析分支；commit 无来源 → None。"""
    from supernova_web.components.scan_store import REPO_SNAPSHOT_FILE, write_repo_snapshot
    scan_dir = tmp_path / "scan"; scan_dir.mkdir()
    write_repo_snapshot(scan_dir, str(_linked_repo_with_head(tmp_path, "dev")))
    data = json.loads((scan_dir / REPO_SNAPSHOT_FILE).read_text())
    assert data == {"branch": "dev", "commit": None}


def test_write_repo_snapshot_no_signal_skips_file(tmp_path):
    """无 meta 无 .git/HEAD（裸目录/损坏）→ 不写文件（读侧缺失→None）。"""
    from supernova_web.components.scan_store import REPO_SNAPSHOT_FILE, write_repo_snapshot
    repo = tmp_path / "bare"; repo.mkdir()
    scan_dir = tmp_path / "scan"; scan_dir.mkdir()
    write_repo_snapshot(scan_dir, str(repo))
    assert not (scan_dir / REPO_SNAPSHOT_FILE).exists()


def test_read_repo_snapshot_roundtrip_and_missing(tmp_path):
    """读回：写后 roundtrip；未写/损坏 → {}（调用方 get 兜 None）。"""
    from supernova_web.components import scan_store
    scan_dir = tmp_path / "scan"; scan_dir.mkdir()
    assert scan_store.read_repo_snapshot(scan_dir) == {}
    scan_store.write_repo_snapshot(scan_dir, str(_repo_with_meta(tmp_path)))
    assert scan_store.read_repo_snapshot(scan_dir) == {"branch": "main", "commit": "abc123def"}
    (scan_dir / scan_store.REPO_SNAPSHOT_FILE).write_text("{broken")
    assert scan_store.read_repo_snapshot(scan_dir) == {}


# ---- ScanSummary 读回 ----

def test_list_scans_includes_repo_snapshot(tmp_path):
    store = ScanStore(tmp_path)
    (tmp_path / WS).mkdir()
    scan_id, scan_dir = store.create_scan(WS, "", "/code/repo")
    from supernova_web.components import scan_store
    scan_store.write_repo_snapshot(scan_dir, str(_repo_with_meta(tmp_path, "dev", "fff000")))
    s = next(x for x in store.list_scans(WS) if x.scan_id == scan_id)
    assert s.repo_branch == "dev"
    assert s.repo_commit == "fff000"
    assert s.as_dict()["repo_branch"] == "dev"


def test_list_scans_without_snapshot_none(tmp_path):
    """存量报告（无快照文件）→ None/None，不报错不显示。"""
    store = ScanStore(tmp_path)
    (tmp_path / WS).mkdir()
    scan_id, _ = store.create_scan(WS, "", "/code/repo")
    s = next(x for x in store.list_scans(WS) if x.scan_id == scan_id)
    assert s.repo_branch is None and s.repo_commit is None


# ---- ScanManager 接线（_maybe_write_repo_snapshot）----

def _scan_manager(tmp_path):
    from supernova_web.components.scan_manager import ScanManager
    ws_dir = tmp_path / "workspaces"; ws_dir.mkdir(exist_ok=True)
    return ScanManager(ws_dir, tmp_path / "repos", None)


def test_maybe_write_repo_snapshot_repo_source(tmp_path):
    """repo 来源（白盒/组合白盒腿）→ 写快照；target 即 _resolve_repo_path 的产物。"""
    from supernova_web.components import scan_store
    from supernova_web.models import RepoSource, ScanRequest
    repo = _repo_with_meta(tmp_path, "feat/x", "deadbee")
    sm = _scan_manager(tmp_path)
    scan_dir = tmp_path / "workspaces" / WS / "scans" / "s1"; scan_dir.mkdir(parents=True)
    req = ScanRequest(type="whitebox", source=RepoSource(kind="repo", value="repo"),
                      workspace=WS)
    sm._maybe_write_repo_snapshot(req, str(repo), scan_dir)
    assert scan_store.read_repo_snapshot(scan_dir) == {"branch": "feat/x", "commit": "deadbee"}


def test_maybe_write_repo_snapshot_non_repo_source_skips(tmp_path):
    """url 来源（黑盒 source=None）→ 不写快照。"""
    from supernova_web.components import scan_store
    from supernova_web.models import ScanRequest
    scan_dir = tmp_path / "workspaces" / WS / "scans" / "s2"; scan_dir.mkdir(parents=True)
    sm = _scan_manager(tmp_path)
    req = ScanRequest(type="blackbox", source=None, workspace=WS,
                      reuse_whitebox_scan_id="wb-1")
    sm._maybe_write_repo_snapshot(req, None, scan_dir)
    assert scan_store.read_repo_snapshot(scan_dir) == {}
