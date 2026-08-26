# packages/web/tests/test_legacy_repo_migration.py
# 守护（2026-08-27）：启动搬迁已退役——web 启动（lifespan 全迁移序列）不得移动全局
# repos/ 下的任何仓库。历史：_migrate_legacy_repos 曾在每次启动把全局 repos/<name>
# （含 .git）物理搬到 workspaces/__legacy__/repos/，2026-08-26 实证搬走在用仓库致扫描
# 当场 "Repository not found"（NodeGoat-20260826-171403，容器重启后仓库被搬、原
# repo_path 失效）。决策：全局 repos/ 视为废弃，启动不碰；手动 clone 进去的仓库经仓库页
# link-dir 显式关联（linked_repos.json，谁关联谁可见）。
#
# 注意路径：cfg.workspaces_dir = resolve_workspaces_dir() = SUPERNOVA_WORKER_ROOT/"workspaces"。
from starlette.testclient import TestClient
from supernova_web.app import create_app


def test_startup_leaves_global_repos_untouched(tmp_path, monkeypatch):
    """lifespan 全启动序列跑完后，全局 repos/ 下含 .git 的仓库必须原样留在原地，
    不产生 workspaces/__legacy__/repos 搬迁。"""
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_web.config import get_config
    get_config.cache_clear()

    (tmp_path / "workspaces").mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repos" / "NodeGoat"
    (repo / ".git").mkdir(parents=True)

    app = create_app()
    with TestClient(app):  # 触发 lifespan 全启动序列
        pass

    # 仓库仍在全局 repos/ 原位（未被搬走）
    assert repo.is_dir() and (repo / ".git").exists(), (
        f"global repo was moved out during startup: {repo}"
    )
    # 未产生任何搬迁目标
    moved = tmp_path / "workspaces" / "__legacy__" / "repos" / "NodeGoat"
    assert not moved.exists(), f"startup relocated repo to {moved}"


def test_no_repos_dir_does_not_crash(tmp_path, monkeypatch):
    """repos_dir 不存在时启动不受影响。"""
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_REPOS_DIR", str(tmp_path / "nonexistent"))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_web.config import get_config
    get_config.cache_clear()

    (tmp_path / "workspaces").mkdir(parents=True, exist_ok=True)

    app = create_app()
    with TestClient(app):
        pass

    # 没崩、没生成 __legacy__
    assert not (tmp_path / "workspaces" / "__legacy__").exists()
