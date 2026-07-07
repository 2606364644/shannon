from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from .config import get_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup（僵尸清理在任务 9 接入 ScanManager 后填充）
    yield
    # shutdown（任务 9 接入 ScanManager 取消在途扫描后填充）


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
    from .components.scan_manager import ScanManager
    from .api import events, fs, multi_configs, scan, system_status, workspaces

    app.state.indexer = WorkspacesIndexer(cfg.workspaces_dir)
    app.state.config_store = MultiRepoConfigStore(cfg.configs_dir)
    git_fetcher = GitFetcher(cfg.repos_dir, cfg.gitlab_user, cfg.gitlab_token)
    overrides = overrides or {}
    app.state.scan_manager = overrides.get("scan_manager") or ScanManager(
        cfg.workspaces_dir, cfg.repos_dir, app.state.config_store,
        max_concurrent=cfg.max_concurrent, scan_timeout=cfg.scan_timeout)

    app.include_router(workspaces.router)
    app.include_router(scan.router)
    app.include_router(multi_configs.router)
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
