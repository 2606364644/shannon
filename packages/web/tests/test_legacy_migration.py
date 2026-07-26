# packages/web/tests/test_legacy_migration.py
from starlette.testclient import TestClient
from supernova_web.app import create_app


def test_legacy_workspace_assigned_to_admins(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    # tmp_workspaces 设 SUPERNOVA_WORKER_ROOT=tmp_path/"workspaces"，而
    # resolve_workspaces_dir() 会再追加 /"workspaces" -> 嵌套一层不存在的目录，
    # 导致 create_app() 里 auth_db_path 创建失败。改回父目录使解析结果恰等于
    # tmp_workspaces（与 conftest.app_with_ws 同款 rebase）。
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    app = create_app()
    admin = app.state.auth_store.create_user("admin", "h", role="admin")
    legacy = app.state.config.workspaces_dir / "legacy_ws"
    legacy.mkdir()
    assert app.state.auth_store.list_workspace_members("legacy_ws") == []  # 迁移前无记录
    with TestClient(app):  # 触发 lifespan -> 迁移
        pass
    assert app.state.auth_store.get_workspace_member_role("legacy_ws", admin.id) == "manager"


def test_workspace_with_members_not_reassigned(tmp_workspaces, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_WEB_COOKIE_SECURE", "0")
    # 同上 rebase，使 workspaces_dir == tmp_workspaces。
    monkeypatch.setenv("SUPERNOVA_WORKER_ROOT", str(tmp_workspaces.parent))
    app = create_app()
    admin = app.state.auth_store.create_user("admin", "h", role="admin")
    alice = app.state.auth_store.create_user("alice", "h")
    (app.state.config.workspaces_dir / "ws1").mkdir()
    app.state.auth_store.add_workspace_member("ws1", alice.id, "manager")  # 已有成员
    with TestClient(app):
        pass
    # admin 不被重复加（已有成员记录的不动）-- 验证仍只有 alice
    members = app.state.auth_store.list_workspace_members("ws1")
    assert len(members) == 1 and members[0][1] == "alice"
