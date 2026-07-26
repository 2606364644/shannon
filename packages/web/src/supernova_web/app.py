from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from .config import get_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    # auth: 启动 seed 预置账号 + 周期清理过期 session
    from .auth.seed import seed_users
    import asyncio
    seed_users(app.state.auth_store, app.state.config.users_seed_file)

    async def _purge_loop():
        while True:
            try:
                app.state.session_manager.purge_expired()
            except Exception:
                pass
            await asyncio.sleep(3600)
    app.state._purge_task = asyncio.create_task(_purge_loop())

    # 启动迁移序列（顺序敏感）：
    #   1) 旧全局 repos/<name> → workspaces/__legacy__/repos/<name>（创建 __legacy__ ws 目录）
    #   2) 给无成员记录的 ws（含刚创建的 __legacy__）分配 admin (manager)
    #   3) per-ws 补写仓库 meta（覆盖 __legacy__ 的搬迁仓库）
    #   4) 重建孤儿 scan 状态
    # purge_loop 与本序列无依赖、保持原位即可。
    _migrate_legacy_repos(app)
    _migrate_legacy_workspace_members(app)
    _reconcile_repo_meta(app)
    await _reconcile_orphaned_scans(app)  # 重启后给孤儿 scan 补 scan_end，让 live 不再卡 running
    yield
    app.state._purge_task.cancel()
    # shutdown（任务 9 接入 ScanManager 取消在途扫描后填充）


async def _reconcile_orphaned_scans(app: FastAPI) -> None:
    """启动时遍历 workspaces，对孤儿 scan（session running 但 worker 已不存活、
    且无 scan_end）补写 scan_end(interrupted) + 失败原因。

    容器重启会杀掉 scan_manager._watch 协程，导致在途 scan 永不写 scan_end、
    session 卡 running、live SSE 空等。此处一次性兜底，使重开 live 页能正常显
    「已中断」+ 原因。单 ws 异常不阻塞启动。
    """
    from .components.orphan_reconciler import reconcile_orphaned
    cfg = app.state.config
    indexer = app.state.indexer
    # 启动时 scan_manager._procs 为空，active_pids()={} -> is_running 对所有 ws=False，
    # 故所有 session running 的 ws 都会被判孤儿并补 scan_end（这正是重启后的真实情况）。
    indexer.sync_active(app.state.scan_manager.active_pids())
    if not cfg.workspaces_dir.is_dir():
        return
    for ws_dir in cfg.workspaces_dir.iterdir():
        if not ws_dir.is_dir():
            continue
        try:
            await reconcile_orphaned(ws_dir, indexer.is_running(ws_dir.name))
        except Exception:
            continue


def _migrate_legacy_workspace_members(app: FastAPI) -> None:
    """把无成员记录的 legacy workspace 分配给所有 admin（manager），让 admin 能见/管。
    已有成员记录的 workspace 不动（不重复分配、不覆盖）。"""
    store = app.state.auth_store
    admins = [u for u in store.list_all_users() if u.role == "admin"]
    if not admins:
        return
    ws_dir = app.state.config.workspaces_dir
    if not ws_dir.is_dir():
        return
    for d in ws_dir.iterdir():
        if not d.is_dir():
            continue
        if store.list_workspace_members(d.name):
            continue  # 已有成员，跳过
        for a in admins:
            store.add_workspace_member(d.name, a.id, "manager")


def _migrate_legacy_repos(app: FastAPI) -> None:
    """启动迁移：把旧全局 ``repos/<name>`` 搬到 ``workspaces/__legacy__/repos/<name>``。

    背景：web repos 隔离 P2 前，所有 clone 仓库落在共享全局 ``repos/`` 下（无 ws 归属）。
    P2 起 repos 按 ws 分桶（``workspaces/<ws>/repos/``），旧全局目录废弃。本函数在启动时
    一次性把残留的旧全局仓库迁入 ``__legacy__`` ws，使其对 admin 可见、可扫。

    仅迁 top-level 且含 ``.git`` 的目录（避免误搬普通文件夹）；目标已存在或源缺失则跳过
    （幂等）。admin 分配由 ``_migrate_legacy_workspace_members`` 复用既有逻辑，**此处不
    重复实现**。单仓库失败不阻塞启动（best-effort）。
    """
    import shutil
    cfg = app.state.config
    old_root = cfg.repos_dir
    if not old_root.is_dir():
        return
    legacy_repos = cfg.workspaces_dir / "__legacy__" / "repos"
    for sub in list(old_root.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        if not (sub / ".git").exists():
            continue  # 仅迁真仓库（含 .git），跳过普通文件夹
        target = legacy_repos / sub.name
        if target.exists():
            continue  # 幂等：目标已存在，跳过（不覆盖）
        try:
            legacy_repos.mkdir(parents=True, exist_ok=True)
            shutil.move(str(sub), str(target))
        except Exception:
            # 单仓库失败不影响其他仓库与整体启动
            continue


def _reconcile_repo_meta(app: FastAPI) -> None:
    """对每个 workspace 跑 RepoManager.migrate_legacy(ws)——为已存在但缺 meta 的仓库
    补写 ``.supernova-repo.json``（含从旧全局迁入 ``__legacy__`` 的仓库）。

    旧名 ``_migrate_legacy_repos`` 与本函数语义不符（migrate_legacy 不搬仓库、只补 meta），
    2026-07-26 web repos isolation P2 Task 7 重命名为 ``_reconcile_repo_meta``，腾出原名
    给「搬旧全局 repos → __legacy__ ws」的新函数。单 ws 异常不阻塞启动（best-effort）。
    """
    cfg = app.state.config
    if not cfg.workspaces_dir.is_dir():
        return
    rm = app.state.repo_manager
    for d in cfg.workspaces_dir.iterdir():
        if not d.is_dir():
            continue
        try:
            rm.migrate_legacy(d.name)
        except Exception:
            # 单 ws 失败不影响其他 ws 与整体启动
            continue


def _mount_frontend(app: FastAPI, cfg) -> None:
    """挂载前端 SPA 静态托管（生产/集成模式）。

    cfg.frontend_dir 为空或目录不存在时直接返回（dev 模式前端走 vite 5173）。
    必须在所有 /api/* 路由与 /health 注册**之后**调用——catch-all 靠 FastAPI
    注册顺序保证 API 优先命中。
    """
    if not cfg.frontend_dir:
        return
    dist = Path(cfg.frontend_dir)
    if not dist.is_dir():
        return
    index_html = dist / "index.html"
    dist_resolved = dist.resolve()
    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/")
    async def _spa_root():
        return FileResponse(index_html)

    @app.get("/{full_path:path}")
    async def _spa_fallback(full_path: str):
        candidate = (dist / full_path).resolve()
        try:
            candidate.relative_to(dist_resolved)
        except ValueError:
            raise HTTPException(status_code=404)
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_html)


def create_app(overrides: dict | None = None) -> FastAPI:
    app = FastAPI(title="Supernova Web", version="0.1.0", lifespan=lifespan)
    cfg = get_config()
    app.state.config = cfg

    from .auth.store import AuthStore
    from .auth.session import SessionManager
    from .auth.middleware import AuthMiddleware
    auth_store = AuthStore(str(cfg.auth_db_path))
    auth_store.init_schema()
    app.state.auth_store = auth_store
    app.state.session_manager = SessionManager(auth_store, ttl_hours=cfg.session_ttl_hours)
    app.add_middleware(AuthMiddleware)

    from .components.workspaces_indexer import WorkspacesIndexer
    from .components.git_fetcher import GitFetcher
    from .components.multi_repo_config_store import MultiRepoConfigStore
    from .components.repo_manager import RepoManager
    from .components.scan_manager import ScanManager
    from .api import events, fs, members, multi_configs, repos, scan, system_status, users, workspaces

    app.state.indexer = WorkspacesIndexer(cfg.workspaces_dir)
    app.state.config_store = MultiRepoConfigStore(cfg.configs_dir)
    git_fetcher = GitFetcher(cfg.repos_dir, cfg.gitlab_user, cfg.gitlab_token)
    overrides = overrides or {}
    app.state.scan_manager = overrides.get("scan_manager") or ScanManager(
        cfg.workspaces_dir, cfg.repos_dir, app.state.config_store,
        max_concurrent=cfg.max_concurrent, scan_timeout=cfg.scan_timeout)
    app.state.repo_manager = overrides.get("repo_manager") or RepoManager(
        cfg.workspaces_dir, git_fetcher, max_concurrent=cfg.repos_max_concurrent_clones)

    from .auth.dependencies import current_user
    _require_auth = [Depends(current_user)]
    app.include_router(workspaces.router, dependencies=_require_auth)
    app.include_router(scan.router, dependencies=_require_auth)
    app.include_router(multi_configs.router, dependencies=_require_auth)
    app.include_router(repos.router, dependencies=_require_auth)
    app.include_router(events.router, dependencies=_require_auth)
    app.include_router(fs.router, dependencies=_require_auth)
    app.include_router(system_status.router, dependencies=_require_auth)
    app.include_router(members.router, dependencies=_require_auth)
    app.include_router(users.router)

    from .auth import routes as auth_routes
    app.include_router(auth_routes.router)

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "git": {
                "binary_available": cfg.git_binary_available,
                "credentials_configured": bool(cfg.gitlab_user and cfg.gitlab_token),
            },
        }

    _mount_frontend(app, cfg)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    cfg = get_config()
    uvicorn.run("supernova_web.app:app", host="0.0.0.0", port=cfg.port, reload=False)
