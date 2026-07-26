from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from supernova_web.auth.dependencies import current_user, require_admin, workspace_member, workspace_manager
from supernova_web.auth.models import User

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def _workspace_path(request: Request, ws: str):
    p = request.app.state.config.workspaces_dir / ws
    if not p.exists():
        raise HTTPException(404, "workspace not found")
    return p


class CreateWorkspaceIn(BaseModel):
    name: str


@router.post("", status_code=201)
async def create_workspace(body: CreateWorkspaceIn, request: Request,
                           user: User = Depends(require_admin)):
    """P1: admin 显式建 workspace (替代原 scan 创建 manager 模型)。

    ws 先于 scan 存在, 为 P2 repo 隔离铺路; admin 自动成为 manager,
    其他成员由 admin 预分配 (P1 Task 5/6)。
    """
    ws = body.name
    ws_dir = request.app.state.config.workspaces_dir / ws
    if ws_dir.exists():
        raise HTTPException(409, "workspace already exists")
    ws_dir.mkdir(parents=True)
    # 写最小 session.json 使新建 ws 在 GET /api/workspaces 可见(final-review I1)。
    # list_workspaces 经 SessionManager.list_workspaces 过滤 (p/session.json).exists();
    # 不写则新建 ws 不可见, admin 无法导航/分配成员。
    # status=completed(非 running): _status_of 视 completed 为终态 -> 返回 completed,
    # 对未扫描的空 ws 正确(不显示 spinner)。不用 SessionManager.create_workspace
    # (它写 status=running, 对未扫描 ws 错误显示运行中)。
    (ws_dir / "session.json").write_text(json.dumps({
        "status": "completed",
        "scan_type": "whitebox",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "web_url": "",
        "repo_path": "",
    }), encoding="utf-8")
    request.app.state.auth_store.add_workspace_member(ws, user.id, "manager")
    return {"name": ws}


@router.get("")
async def list_workspaces(request: Request, user: User = Depends(current_user)):
    idx = request.app.state.indexer
    idx.sync_active(request.app.state.scan_manager.active_pids())
    all_ws = idx.list_workspaces()
    if user.role == "admin":
        return all_ws
    allowed = set(request.app.state.auth_store.list_user_workspaces(user.id))
    return [w for w in all_ws if w["name"] in allowed]


@router.get("/{ws}")
async def get_workspace(ws: str, request: Request, _: User = Depends(workspace_member)):
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
async def delete_workspace(ws: str, request: Request, _: User = Depends(workspace_manager)):
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
    request.app.state.auth_store.delete_workspace_members(ws)
    return {"deleted": ws}


@router.get("/{ws}/deliverables")
async def deliverables_summary(ws: str, request: Request, _: User = Depends(workspace_member), path: str | None = Query(None)):
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
async def deliverables_file(ws: str, filename: str, request: Request, _: User = Depends(workspace_member), track: str = "whitebox"):
    from supernova_web.components.deliverables_reader import DeliverablesReader
    try:
        return DeliverablesReader(_workspace_path(request, ws)).read(filename, track)
    except FileNotFoundError:
        raise HTTPException(404, "file not found")


@router.get("/{ws}/report", response_class=PlainTextResponse)
async def report(ws: str, request: Request, _: User = Depends(workspace_member)):
    from supernova_web.components.deliverables_reader import DeliverablesReader
    reader = DeliverablesReader(_workspace_path(request, ws))
    reports = reader.list_reports()
    chosen = next((x for x in reports if "comprehensive" in x.lower()), reports[0] if reports else None)
    if not chosen:
        # 无报告产物 → 200 空文本:前端 ReportTab Empty「报告尚未生成」契约。
        # workspace 不存在已由 _workspace_path 抛 404,这里只处理「存在但无报告」。
        return ""
    body = reader.read(chosen)
    poc = reader.read_poc()
    if poc:
        # 方向 a1:PoC md 自带「# 可利用漏洞 PoC 集合」一级标题 + 概览/置信度统计,
        # 整体作为报告尾部章节,--- 分隔线让 MarkdownView 渲染 <hr> 划清边界。
        # PoC 含 ```bash/```http 围栏块,前端复用现成 rehype-highlight 语法高亮 + 复制按钮,零前端改动。
        # 无 PoC(扫描中断/PoC activity 未跑)则只返综合报告。
        return f"{body.rstrip()}\n\n---\n\n{poc.lstrip()}"
    return body


@router.get("/{ws}/logs")
async def logs(ws: str, request: Request, _: User = Depends(workspace_member), file: str | None = Query(None)):
    from supernova_web.components.deliverables_reader import DeliverablesReader
    reader = DeliverablesReader(_workspace_path(request, ws))
    if file is None:
        return {"files": reader.list_logs()}
    try:
        return {"content": reader.read_log(file)}
    except FileNotFoundError:
        raise HTTPException(404, "log not found")
