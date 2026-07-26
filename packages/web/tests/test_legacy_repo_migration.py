# packages/web/tests/test_legacy_repo_migration.py
# Task 7 (web repos isolation P2): 启动时把旧全局 repos/<name> 迁到
# workspaces/__legacy__/repos/<name>，并让 __legacy__ ws 自动分配给所有 admin (manager)。
#
# 注意路径：cfg.workspaces_dir = resolve_workspaces_dir() = SUPERNOVA_WORKER_ROOT/"workspaces"。
# 故 SUPERNOVA_WORKER_ROOT=tmp_path 时，目标 = tmp_path/"workspaces"/"__legacy__"/"repos"/<name>
# (单层 "workspaces"，非双层——brief 写错了，本测试按真实解析路径断言)。
import shutil
from pathlib import Path
from starlette.testclient import TestClient
from supernova_web.app import create_app


def test_legacy_repos_moved_to_legacy_ws(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    # get_config 是 @lru_cache，env 改后必须清缓存，否则会读到上个测试的旧 cfg。
    from supernova_web.config import get_config
    get_config.cache_clear()

    # 预建 workspaces 父目录（auth.db 落盘需要）+ 旧全局 repos/<oldrepo>/.git
    (tmp_path / "workspaces").mkdir(parents=True, exist_ok=True)
    legacy_repo = tmp_path / "repos" / "oldrepo"
    (legacy_repo / ".git").mkdir(parents=True)

    app = create_app()
    admin = app.state.auth_store.create_user("admin", "h", role="admin")

    with TestClient(app):  # 触发 lifespan -> _migrate_legacy_repos
        pass

    # 旧仓库被搬到 __legacy__ ws 下（单层 "workspaces" 路径）
    target = tmp_path / "workspaces" / "__legacy__" / "repos" / "oldrepo"
    assert target.exists(), f"expected moved repo at {target}, found: {list(target.parent.glob('*')) if target.parent.exists() else 'no parent'}"
    # 旧位置应已不存在
    assert not legacy_repo.exists()

    # __legacy__ ws 自动分配给所有 admin (manager) —— 复用 _migrate_legacy_workspace_members
    assert app.state.auth_store.get_workspace_member_role("__legacy__", admin.id) == "manager"


def test_legacy_repos_migration_idempotent(tmp_path, monkeypatch):
    """二次启动不重复搬迁 / 不覆盖已存在的目标（已是目标位置的仓库不动）。"""
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_web.config import get_config
    get_config.cache_clear()

    (tmp_path / "workspaces").mkdir(parents=True, exist_ok=True)
    (tmp_path / "repos" / "oldrepo" / ".git").mkdir(parents=True)

    # 第一次启动：迁移
    app1 = create_app()
    with TestClient(app1):
        pass
    target = tmp_path / "workspaces" / "__legacy__" / "repos" / "oldrepo"
    assert target.exists()

    # 模拟遗留：再次放一个同名旧仓库到 repos/（罕见但需保证不覆盖已迁的）
    (tmp_path / "repos" / "oldrepo" / ".git").mkdir(parents=True)

    # 第二次启动：source 仍在但 target 已存在 → 应跳过（不覆盖、不报错）
    get_config.cache_clear()
    app2 = create_app()
    with TestClient(app2):
        pass

    # target 仍存在；source 未被搬走（因 target 已存在，被跳过）
    assert target.exists()


def test_non_git_dirs_not_moved(tmp_path, monkeypatch):
    """旧 repos/ 下不含 .git 的目录（普通文件夹）不视为仓库、不搬迁。"""
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_path))
    monkeypatch.setenv("SUPERNOVA_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    from supernova_web.config import get_config
    get_config.cache_clear()

    (tmp_path / "workspaces").mkdir(parents=True, exist_ok=True)
    # 一个有 .git 的真仓库 + 一个无 .git 的普通目录
    (tmp_path / "repos" / "real" / ".git").mkdir(parents=True)
    (tmp_path / "repos" / "junk").mkdir(parents=True)

    app = create_app()
    with TestClient(app):
        pass

    # real 被迁走，junk 留在原地
    assert (tmp_path / "workspaces" / "__legacy__" / "repos" / "real").exists()
    assert (tmp_path / "repos" / "junk").exists()
    assert not (tmp_path / "repos" / "real").exists()


def test_no_repos_dir_does_not_crash(tmp_path, monkeypatch):
    """旧 repos_dir 不存在时迁移函数直接返回，不影响启动。"""
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
