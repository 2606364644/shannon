from supernova_core.correlation.drift import detect_drift


def test_no_drift_when_repo_older():
    # workspace 创建晚于 repo 改动 → 无漂移
    r = detect_drift(workspace_created_at=2000.0, repo_mtime=1000.0)
    assert r.drifted is False


def test_drift_when_repo_newer():
    # repo 在 workspace 创建后改过 → 漂移
    r = detect_drift(workspace_created_at=1000.0, repo_mtime=2000.0)
    assert r.drifted is True
    assert "漂移" in r.note
