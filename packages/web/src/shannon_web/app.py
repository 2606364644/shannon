from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup（僵尸清理在任务 9 接入 ScanManager 后填充）
    yield
    # shutdown（任务 9 接入 ScanManager 取消在途扫描后填充）


def create_app() -> FastAPI:
    app = FastAPI(title="Shannon Web", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "git_available": get_config().git_available}

    # 路由由任务 10/11 注册：app.include_router(...)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    cfg = get_config()
    uvicorn.run("shannon_web.app:app", host="0.0.0.0", port=cfg.port, reload=False)
