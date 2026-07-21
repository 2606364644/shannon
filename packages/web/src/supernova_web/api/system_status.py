from __future__ import annotations

import asyncio
import os
import socket
from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["system-status"])


async def _probe_temporal() -> tuple[str, str | None]:
    """轻量 socket 探测 Temporal 可达性(复用 scan_manager._check_temporal 同款逻辑)。

    非 ICMP ping,1s 超时;设置页低频打开可接受(对 spec §5.3 "不做实时 ping" 的细化)。
    """
    host = os.environ.get("SUPERNOVA_TEMPORAL_HOST", "localhost")
    port = int(os.environ.get("SUPERNOVA_TEMPORAL_PORT", "7233"))

    def _probe() -> tuple[bool, str | None]:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True, None
        except OSError as e:
            return False, str(e)

    loop = asyncio.get_running_loop()
    ok, err = await loop.run_in_executor(None, _probe)
    return ("connected", None) if ok else ("error", err)


@router.get("/system-status")
async def system_status(request: Request) -> dict:
    cfg = request.app.state.config
    last_status, last_error = await _probe_temporal()
    try:
        ver = version("supernova-web")
    except PackageNotFoundError:
        ver = "unknown"
    return {
        "ai_provider": os.environ.get("SUPERNOVA_AI_PROVIDER", "claude"),
        "browser_engine": os.environ.get("SUPERNOVA_BROWSER_ENGINE", "agent-browser"),
        "temporal": {
            "enabled": True,
            "host": f'{os.environ.get("SUPERNOVA_TEMPORAL_HOST", "localhost")}:{os.environ.get("SUPERNOVA_TEMPORAL_PORT", "7233")}',
            "last_status": last_status,
            "last_error": last_error,
        },
        "git": {
            "binary_available": cfg.git_binary_available,
            "credentials_configured": bool(cfg.gitlab_user and cfg.gitlab_token),
        },
        "version": f"supernova-web {ver}",
        # 平台品牌名(左上角字标数据源);由 SUPERNOVA_WEB_BRAND_NAME env 驱动,默认 Supernova。
        "brand_name": cfg.brand_name,
    }
