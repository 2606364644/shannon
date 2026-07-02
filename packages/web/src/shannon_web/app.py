from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup（僵尸清理在任务 9 接入 ScanManager 后填充）
    yield
    # shutdown（任务 9 接入 ScanManager 取消在途扫描后填充）


def create_app(overrides: dict | None = None) -> FastAPI:
    app = FastAPI(title="Shannon Web", version="0.1.0", lifespan=lifespan)
    cfg = get_config()
    app.state.config = cfg

    from .components.workspaces_indexer import WorkspacesIndexer
    from .components.git_fetcher import GitFetcher
    from .components.multi_repo_config_store import MultiRepoConfigStore
    from .components.scan_manager import ScanManager
    from .api import workspaces, scan, multi_configs

    app.state.indexer = WorkspacesIndexer(cfg.workspaces_dir)
    app.state.config_store = MultiRepoConfigStore(cfg.configs_dir)
    git_fetcher = GitFetcher(cfg.repos_dir, cfg.gitlab_user, cfg.gitlab_token)
    overrides = overrides or {}
    app.state.scan_manager = overrides.get("scan_manager") or ScanManager(
        cfg.workspaces_dir, cfg.repos_dir, app.state.config_store, git_fetcher,
        max_concurrent=cfg.max_concurrent, scan_timeout=cfg.scan_timeout)

    app.include_router(workspaces.router)
    app.include_router(scan.router)
    app.include_router(multi_configs.router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "git_available": cfg.git_available}

    return app


app = create_app()


def main() -> None:
    import uvicorn

    cfg = get_config()
    uvicorn.run("shannon_web.app:app", host="0.0.0.0", port=cfg.port, reload=False)
