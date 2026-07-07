from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from shannon_web.components.scan_manager import TemporalUnavailable, TooManyScans
from shannon_web.models import ScanAccepted, ScanRequest

router = APIRouter(prefix="/api/scan", tags=["scan"])


@router.post("", response_model=ScanAccepted, status_code=202)
async def create_scan(req: ScanRequest, request: Request):
    sm = request.app.state.scan_manager
    try:
        ws = await sm.start(req)
    except TemporalUnavailable:
        raise HTTPException(400, "Temporal 服务未运行，请先 docker-compose up -d")
    except TooManyScans as e:
        raise HTTPException(409, f"已有扫描在跑，并发上限 {e.limit}")
    except PermissionError as e:
        # OS-level EACCES/EPERM from ws_dir.mkdir()（git-creds 来源已于 Task 3 移除）
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except ValidationError as e:  # correlation yaml 校验失败
        raise HTTPException(422, detail=e.errors())
    return ScanAccepted(workspace=ws)


@router.delete("/{ws}")
async def cancel_scan(ws: str, request: Request):
    ok = await request.app.state.scan_manager.cancel(ws)
    if not ok:
        raise HTTPException(404, "scan not found")
    return {"cancelled": ws}
