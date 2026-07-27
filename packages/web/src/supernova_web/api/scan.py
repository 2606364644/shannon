from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from supernova_web.auth.dependencies import current_user, workspace_member
from supernova_web.auth.models import User
from supernova_web.components.scan_manager import TemporalUnavailable, TooManyScans
from supernova_web.models import ScanAccepted, ScanRequest

router = APIRouter(prefix="/api/scan", tags=["scan"])


@router.post("", response_model=ScanAccepted, status_code=202)
async def create_scan(req: ScanRequest, request: Request,
                      user: User = Depends(current_user)):
    # P1: scan 必须在 admin 预建好的 ws 内跑 (替代原 scan 创建 manager 模型)。
    # ws 先于 scan 存在, 为 P2 repo 隔离铺路; 这里只校验, 不创建。
    ws = req.workspace
    ws_dir = request.app.state.config.workspaces_dir / ws if ws else None
    if not ws or not ws_dir.is_dir():
        raise HTTPException(422, "workspace 不存在，请先让 admin 创建")
    if user.role != "admin" and request.app.state.auth_store.get_workspace_member_role(
            ws, user.id) is None:
        raise HTTPException(403, "非该 workspace 成员")
    sm = request.app.state.scan_manager
    try:
        ws_name, scan_id = await sm.start(req)
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
    return ScanAccepted(workspace=ws_name, scan_id=scan_id)
