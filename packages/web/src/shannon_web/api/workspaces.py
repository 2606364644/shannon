from __future__ import annotations

import shutil

from fastapi import APIRouter, HTTPException, Request

from shannon_web.components.workspaces_indexer import WorkspacesIndexer

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
    for row in idx.list_workspaces():
        if row["name"] == ws:
            return row
    raise HTTPException(404, "workspace not found")


@router.delete("/{ws}")
async def delete_workspace(ws: str, request: Request):
    p = _workspace_path(request, ws)  # 404 if 不存在
    idx = request.app.state.indexer
    active = request.app.state.scan_manager.active_pids()
    # scan_manager 跟踪本进程 spawn 的子进程；indexer 在先前请求中 sync 过 pid（或
    # 测试直接注入）。任一来源有 alive pid 即视为运行中，避免误删。
    pid = active.get(ws)
    if pid is None:
        pid = getattr(idx, "_active_pids", {}).get(ws)
    if pid and WorkspacesIndexer._pid_alive(pid):
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


@router.get("/{ws}/report")
async def report(ws: str, request: Request):
    from shannon_web.components.deliverables_reader import DeliverablesReader
    reader = DeliverablesReader(_workspace_path(request, ws))
    reports = reader.summary().get("reports", [])
    chosen = next((x for x in reports if "comprehensive" in x.lower()), reports[0] if reports else None)
    if not chosen:
        raise HTTPException(404, "no report")
    return reader.read(chosen)


@router.get("/{ws}/logs")
async def logs(ws: str, request: Request, name: str = "workflow.log"):
    from shannon_web.components.deliverables_reader import DeliverablesReader
    try:
        return DeliverablesReader(_workspace_path(request, ws)).read_log(name)
    except FileNotFoundError:
        raise HTTPException(404, "log not found")
