from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from .config import get_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.repo_manager.migrate_legacy()  # 旧 repos 目录纳入管理
    await _reconcile_orphaned_scans(app)  # 重启后给孤儿 scan 补 scan_end，让 live 不再卡 running
    yield
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
    app = FastAPI(title="Shannon Web", version="0.1.0", lifespan=lifespan)
    cfg = get_config()
    app.state.config = cfg

    from .components.workspaces_indexer import WorkspacesIndexer
    from .components.git_fetcher import GitFetcher
    from .components.multi_repo_config_store import MultiRepoConfigStore
    from .components.repo_manager import RepoManager
    from .components.scan_manager import ScanManager
    from .api import events, fs, multi_configs, repos, scan, system_status, workspaces

    app.state.indexer = WorkspacesIndexer(cfg.workspaces_dir)
    app.state.config_store = MultiRepoConfigStore(cfg.configs_dir)
    git_fetcher = GitFetcher(cfg.repos_dir, cfg.gitlab_user, cfg.gitlab_token)
    overrides = overrides or {}
    app.state.scan_manager = overrides.get("scan_manager") or ScanManager(
        cfg.workspaces_dir, cfg.repos_dir, app.state.config_store,
        max_concurrent=cfg.max_concurrent, scan_timeout=cfg.scan_timeout)
    app.state.repo_manager = overrides.get("repo_manager") or RepoManager(
        cfg.repos_dir, git_fetcher, max_concurrent=cfg.repos_max_concurrent_clones)

    app.include_router(workspaces.router)
    app.include_router(scan.router)
    app.include_router(multi_configs.router)
    app.include_router(repos.router)
    app.include_router(events.router)
    app.include_router(fs.router)
    app.include_router(system_status.router)

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
    uvicorn.run("shannon_web.app:app", host="0.0.0.0", port=cfg.port, reload=False)
