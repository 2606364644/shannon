from __future__ import annotations

import json
import shutil

from fastapi import APIRouter, HTTPException, Query, Request
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
    from supernova_core.session import SessionManager
    from supernova_web.components.metrics_normalizer import normalize_metrics
    from supernova_web.components.workspaces_indexer import _to_unix
    mgr = SessionManager(request.app.state.config.workspaces_dir)
    data = mgr.get_session_data(p)
    return {
        "web_url": mgr.get_web_url(p),
        "repo_path": data.get("repo_path"),
        "scan_type": mgr.get_scan_type(p),
        "status": idx._status_of(p, mgr.get_status(p)),
        "created_at": _to_unix(mgr.get_created_at(p)),
        "completed_at": _to_unix(mgr.get_completed_at(p)),
        "links": data.get("links", {}),
        # metrics 归一化:agents 兼容旧格式(final_duration_ms/total_cost_usd/status/
        # attempts[]),统一到 types.ts SessionMetrics schema;phases 透传不动。
        "metrics": normalize_metrics(data.get("metrics", {})),
        "session": data.get("session", {}),
    }


@router.delete("/{ws}")
async def delete_workspace(ws: str, request: Request):
    p = _workspace_path(request, ws)  # 404 if 不存在
    idx = request.app.state.indexer
    # 活跃判定改用 _status_of==running(终态优先 + heartbeat,spec §4.7)。cancel 标
    # cancelled(终态)后 _status_of≠running → 立即可删;heartbeat fresh(在跑)→ running
    # → 409 先 cancel。不再依赖 pid 表(容器非 host PID namespace 看不到 host pid)。
    from supernova_core.session import SessionManager
    mgr = SessionManager(request.app.state.config.workspaces_dir)
    if idx._status_of(p, mgr.get_status(p)) == "running":
        raise HTTPException(status_code=409, detail="workspace running, cancel scan first")
    shutil.rmtree(p)
    idx.set_active_pid(ws, None)
    return {"deleted": ws}


@router.get("/{ws}/deliverables")
async def deliverables_summary(ws: str, request: Request, path: str | None = Query(None)):
    from supernova_web.components.deliverables_reader import DeliverablesReader
    reader = DeliverablesReader(_workspace_path(request, ws))
    if path is None:
        return reader.summary()
    # ?path=whitebox/xxx → 文件内容 text/plain(前端 FilePreview apiGetText 打此端点)
    parts = path.split("/", 1)
    if len(parts) == 2 and parts[0] in ("whitebox", "blackbox"):
        track, filename = parts[0], parts[1]
    else:
        track, filename = "whitebox", path  # legacy 兜底(无 track 前缀)
    try:
        content = reader.read(filename, track)
    except FileNotFoundError:
        raise HTTPException(404, "file not found")
    if isinstance(content, str):
        return PlainTextResponse(content)
    return PlainTextResponse(json.dumps(content, ensure_ascii=False, indent=2))


@router.get("/{ws}/deliverables/{filename}")
async def deliverables_file(ws: str, filename: str, request: Request, track: str = "whitebox"):
    from supernova_web.components.deliverables_reader import DeliverablesReader
    try:
        return DeliverablesReader(_workspace_path(request, ws)).read(filename, track)
    except FileNotFoundError:
        raise HTTPException(404, "file not found")


@router.get("/{ws}/report", response_class=PlainTextResponse)
async def report(ws: str, request: Request):
    from supernova_web.components.deliverables_reader import DeliverablesReader
    reader = DeliverablesReader(_workspace_path(request, ws))
    reports = reader.list_reports()
    chosen = next((x for x in reports if "comprehensive" in x.lower()), reports[0] if reports else None)
    if not chosen:
        # 无报告产物 → 200 空文本:前端 ReportTab Empty「报告尚未生成」契约。
        # workspace 不存在已由 _workspace_path 抛 404,这里只处理「存在但无报告」。
        return ""
    return reader.read(chosen)


@router.get("/{ws}/logs")
async def logs(ws: str, request: Request, file: str | None = Query(None)):
    from supernova_web.components.deliverables_reader import DeliverablesReader
    reader = DeliverablesReader(_workspace_path(request, ws))
    if file is None:
        return {"files": reader.list_logs()}
    try:
        return {"content": reader.read_log(file)}
    except FileNotFoundError:
        raise HTTPException(404, "log not found")
