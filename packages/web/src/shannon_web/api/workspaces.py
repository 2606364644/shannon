from __future__ import annotations

import shutil

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def _workspace_path(request: Request, ws: str):
    p = request.app.state.config.workspaces_dir / ws
    if not p.exists():
        raise HTTPException(404, "workspace not found")
    return p


@router.get("")
async def list_workspaces(request: Request):
    idx = request.app.state.indexer
    idx.sync_active(request.app.state.scan_manager.active_pids())
    return idx.list_workspaces()


@router.get("/{ws}")
async def get_workspace(ws: str, request: Request):
    idx = request.app.state.indexer
    idx.sync_active(request.app.state.scan_manager.active_pids())
    p = _workspace_path(request, ws)  # 404 if 不存在
    from shannon_core.session import SessionManager
    from shannon_web.components.workspaces_indexer import _to_unix
    mgr = SessionManager(request.app.state.config.workspaces_dir)
    data = mgr.get_session_data(p)
    return {
        "web_url": mgr.get_web_url(p),
        "repo_path": data.get("repo_path"),
        "scan_type": mgr.get_scan_type(p),
        "status": idx._status_of(ws, mgr.get_status(p)),
        "created_at": _to_unix(mgr.get_created_at(p)),
        "completed_at": _to_unix(mgr.get_completed_at(p)),
        "links": data.get("links", {}),
        # metrics 透传:子树结构与前端 SessionMetrics 完全一致(phases/agents 字段对齐)
        "metrics": data.get("metrics", {}),
        "session": data.get("session", {}),
    }


@router.delete("/{ws}")
async def delete_workspace(ws: str, request: Request):
    p = _workspace_path(request, ws)  # 404 if 不存在
    idx = request.app.state.indexer
    # indexer 的 _active_pids 已由 GET /api/workspaces(及单 ws GET)端点的 sync_active
    # 从 scan_manager 同步过来；DELETE 不再重复 sync（替换式 sync 会 wipe 测试直接
    # set_active_pid 注入或跨请求留存的 pid），直接用公开 is_running 访问器判定，
    # 避免 reach into _active_pids / _pid_alive，并消除原 dual-source "either alive" 逻辑。
    if idx.is_running(ws):
        raise HTTPException(status_code=409, detail="workspace running, cancel scan first")
    shutil.rmtree(p)
    idx.set_active_pid(ws, None)
    return {"deleted": ws}


@router.get("/{ws}/deliverables")
async def deliverables_summary(ws: str, request: Request):
    from shannon_web.components.deliverables_reader import DeliverablesReader
    return DeliverablesReader(_workspace_path(request, ws)).summary()


@router.get("/{ws}/deliverables/{filename}")
async def deliverables_file(ws: str, filename: str, request: Request, track: str = "whitebox"):
    from shannon_web.components.deliverables_reader import DeliverablesReader
    try:
        return DeliverablesReader(_workspace_path(request, ws)).read(filename, track)
    except FileNotFoundError:
        raise HTTPException(404, "file not found")


@router.get("/{ws}/report", response_class=PlainTextResponse)
async def report(ws: str, request: Request):
    from shannon_web.components.deliverables_reader import DeliverablesReader
    reader = DeliverablesReader(_workspace_path(request, ws))
    reports = reader.list_reports()
    chosen = next((x for x in reports if "comprehensive" in x.lower()), reports[0] if reports else None)
    if not chosen:
        # 无报告产物 → 200 空文本:前端 ReportTab Empty「报告尚未生成」契约。
        # workspace 不存在已由 _workspace_path 抛 404,这里只处理「存在但无报告」。
        return ""
    return reader.read(chosen)


@router.get("/{ws}/logs")
async def logs(ws: str, request: Request, name: str = "workflow.log"):
    from shannon_web.components.deliverables_reader import DeliverablesReader
    try:
        return DeliverablesReader(_workspace_path(request, ws)).read_log(name)
    except FileNotFoundError:
        raise HTTPException(404, "log not found")
