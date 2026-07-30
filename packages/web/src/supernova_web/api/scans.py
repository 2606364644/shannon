"""T4: scan-scoped API 路由（1 ws : N scans）。

挂在 /api/workspaces（路径 /{ws}/scans/...）。所有路由 Depends(workspace_member)--
能访问 ws 就能访问该 ws 所有 scan（与 P2 repo 同模型，scan 不引入独立 ACL）。
scan_id 路径校验：ScanStore.get_scan_dir 拒 ..//（防路径遍历）。

shim（api/workspaces.py 的 GET /{ws}、/{ws}/report|deliverables|logs、api/events.py 的
GET /{ws}/events、api/scan.py 的 DELETE /api/scan/{ws}）转发到 latest scan，供旧前端不破。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from supernova_web.auth.dependencies import current_user, workspace_member
from supernova_web.auth.models import User
from supernova_web.components.deliverables_reader import DeliverablesReader

router = APIRouter(prefix="/api/workspaces", tags=["scans"])

# 跨 ws 扫描聚合（IA 重设计 §3.1/§7.1）：独立 prefix /api/scans，不属于 /{ws}/scans 命名空间。
# 不能挂 router（prefix=/api/workspaces）--@router.get("") 会撞 workspaces.py 的列表路由。
cross_ws_router = APIRouter(prefix="/api/scans", tags=["scans"])


def _store(request: Request):
    from supernova_web.components.scan_store import ScanStore
    return ScanStore(request.app.state.config.workspaces_dir)


@cross_ws_router.get("")
async def list_all_scans(request: Request, user: User = Depends(current_user)):
    """跨 ws 扫描聚合（IA 重设计 §3.1/§7.1）。admin 见全部 ws 扫描，
    普通用户只见归属 ws（list_user_workspaces）的扫描。每条注入 workspace 字段，
    按 created_at 倒序。ws 量通常个位数到几十，每 ws list_scans 是目录扫描，可接受。"""
    from supernova_web.components.scan_store import ScanStore
    cfg = request.app.state.config
    indexer = request.app.state.indexer
    store = ScanStore(cfg.workspaces_dir)
    if user.role == "admin":
        ws_names = [w["name"] for w in indexer.list_workspaces()]
    else:
        ws_names = request.app.state.auth_store.list_user_workspaces(user.id)
    out = []
    for ws in ws_names:
        for s in store.list_scans(ws):
            d = s.as_dict()
            d["workspace"] = ws
            out.append(d)
    out.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return out


def _scan_dir_or_404(request: Request, ws: str, scan_id: str):
    """按 (ws, scan_id) 定位 scan 目录，路径校验拒越界；None -> 404。"""
    scan_dir = _store(request).get_scan_dir(ws, scan_id)
    if scan_dir is None:
        raise HTTPException(404, "scan not found")
    return scan_dir


def _scan_detail(request: Request, ws: str, scan_id: str, scan_dir) -> dict:
    """scan 详情 payload（同旧 GET /{ws} SessionData shape，读 scan_dir session.json）。"""
    from supernova_core.session import SessionManager
    from supernova_web.components.metrics_normalizer import normalize_metrics
    from supernova_web.components.workspaces_indexer import _to_unix
    from supernova_web.components.scan_store import resolve_workflow_id
    mgr = SessionManager(scan_dir.parent)
    data = mgr.get_session_data(scan_dir)
    idx = request.app.state.indexer
    return {
        "web_url": mgr.get_web_url(scan_dir),
        "repo_path": data.get("repo_path"),
        "scan_type": mgr.get_scan_type(scan_dir),
        "status": idx._status_of(scan_dir, mgr.get_status(scan_dir)),
        "created_at": _to_unix(mgr.get_created_at(scan_dir)),
        "completed_at": _to_unix(mgr.get_completed_at(scan_dir)),
        "links": data.get("links", {}),
        "metrics": normalize_metrics(data.get("metrics", {})),
        "session": data.get("session", {}),
        "workflow_id": resolve_workflow_id(ws, scan_dir, scan_id),
    }


# ── 共享视图（scans.py 路由 + workspaces.py shim 转发共用）─────────────────────

def deliverables_summary_for(scan_dir, path: str | None):
    reader = DeliverablesReader(scan_dir)
    if path is None:
        return reader.summary()
    parts = path.split("/", 1)
    if len(parts) == 2 and parts[0] in ("whitebox", "blackbox"):
        track, filename = parts[0], parts[1]
    else:
        track, filename = "whitebox", path  # legacy 兜底（无 track 前缀）
    try:
        content = reader.read(filename, track)
    except FileNotFoundError:
        raise HTTPException(404, "file not found")
    if isinstance(content, str):
        return PlainTextResponse(content)
    import json
    return PlainTextResponse(json.dumps(content, ensure_ascii=False, indent=2))


def deliverables_file_for(scan_dir, filename: str, track: str = "whitebox"):
    try:
        return DeliverablesReader(scan_dir).read(filename, track)
    except FileNotFoundError:
        raise HTTPException(404, "file not found")


def report_for(scan_dir) -> str:
    reader = DeliverablesReader(scan_dir)
    reports = reader.list_reports()
    chosen = next((x for x in reports if "comprehensive" in x.lower()), reports[0] if reports else None)
    if not chosen:
        return ""  # 无报告产物 -> 200 空文本
    body = reader.read(chosen)
    poc = reader.read_poc()
    if poc:
        return f"{body.rstrip()}\n\n---\n\n{poc.lstrip()}"
    return body


def logs_for(scan_dir, file: str | None):
    reader = DeliverablesReader(scan_dir)
    if file is None:
        return {"files": reader.list_logs()}
    try:
        return {"content": reader.read_log(file)}
    except FileNotFoundError:
        raise HTTPException(404, "log not found")


# ── scan-scoped 路由 ────────────────────────────────────────────────────────

@router.get("/{ws}/scans")
async def list_scans(ws: str, request: Request, _: User = Depends(workspace_member)):
    return [s.as_dict() for s in _store(request).list_scans(ws)]


@router.get("/{ws}/scans/{scan_id}")
async def get_scan(ws: str, scan_id: str, request: Request, _: User = Depends(workspace_member)):
    return _scan_detail(request, ws, scan_id, _scan_dir_or_404(request, ws, scan_id))


@router.get("/{ws}/scans/{scan_id}/deliverables")
async def scan_deliverables_summary(ws: str, scan_id: str, request: Request,
                                    _: User = Depends(workspace_member),
                                    path: str | None = Query(None)):
    return deliverables_summary_for(_scan_dir_or_404(request, ws, scan_id), path)


@router.get("/{ws}/scans/{scan_id}/deliverables/{filename}")
async def scan_deliverables_file(ws: str, scan_id: str, filename: str, request: Request,
                                 _: User = Depends(workspace_member),
                                 track: str = "whitebox"):
    return deliverables_file_for(_scan_dir_or_404(request, ws, scan_id), filename, track)


@router.get("/{ws}/scans/{scan_id}/report", response_class=PlainTextResponse)
async def scan_report(ws: str, scan_id: str, request: Request, _: User = Depends(workspace_member)):
    return report_for(_scan_dir_or_404(request, ws, scan_id))


@router.get("/{ws}/scans/{scan_id}/logs")
async def scan_logs(ws: str, scan_id: str, request: Request, _: User = Depends(workspace_member),
                    file: str | None = Query(None)):
    return logs_for(_scan_dir_or_404(request, ws, scan_id), file)


@router.get("/{ws}/scans/{scan_id}/events")
async def scan_events(ws: str, scan_id: str, request: Request, _: User = Depends(workspace_member)):
    scan_dir = _scan_dir_or_404(request, ws, scan_id)
    from .events import build_scan_events_response
    return await build_scan_events_response(request, scan_dir)


@router.delete("/{ws}/scans/{scan_id}")
async def delete_scan(ws: str, scan_id: str, request: Request, _: User = Depends(workspace_member)):
    """删除单个 scan（真删目录，spec §5.1 DELETE）。

    DELETE 语义=删资源（同 delete_workspace）；取消走 POST /{ws}/scans/{scan_id}/cancel。
    running scan -> 409（先取消再删，避免删在跑 workflow 的目录致状态不一致）；不存在 -> 404。
    """
    from supernova_web.components.scan_manager import ScanRunning
    sm = request.app.state.scan_manager
    try:
        result = await sm.delete(ws, scan_id)
    except ScanRunning as e:
        raise HTTPException(409, str(e))
    if result is None:
        raise HTTPException(404, "scan not found")
    return result


@router.post("/{ws}/scans/{scan_id}/cancel")
async def cancel_scan(ws: str, scan_id: str, request: Request, _: User = Depends(workspace_member)):
    """取消 scan（动作型 POST，对齐 resume POST 子路径风格）。

    web 自起 -> handle.cancel；host 在跑 -> cancel.requested；已死 -> 标 cancelled。
    不存在 -> 404。
    """
    sm = request.app.state.scan_manager
    result = await sm.cancel(ws, scan_id)
    if result is None:
        raise HTTPException(404, "scan not found")
    return result


@router.post("/{ws}/scans/{scan_id}/resume", status_code=202)
async def resume_scan(ws: str, scan_id: str, request: Request, _: User = Depends(workspace_member)):
    """resume 已停未完成的 scan（interrupted/crashed）。

    completed/failed/cancelled/running -> 422（用重扫 POST /api/scan 起新 scan，旧记录保留）。
    scan 不存在 -> 404。
    """
    from supernova_web.components.scan_manager import TemporalUnavailable, TooManyScans
    sm = request.app.state.scan_manager
    try:
        ws_name, scan_id_out = await sm.resume(ws, scan_id)
    except ValueError as e:
        msg = str(e)
        if "不存在" in msg:
            raise HTTPException(404, msg)
        raise HTTPException(422, msg)
    except TemporalUnavailable:
        raise HTTPException(400, "Temporal 服务未运行，请先 docker-compose up -d")
    except TooManyScans as e:
        raise HTTPException(409, f"已有扫描在跑，并发上限 {e.limit}")
    return {"workspace": ws_name, "scan_id": scan_id_out}
