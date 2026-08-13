"""T4: scan-scoped API 路由（1 ws : N scans）。

挂在 /api/workspaces（路径 /{ws}/scans/...）。所有路由 Depends(workspace_member)--
能访问 ws 就能访问该 ws 所有 scan（与 P2 repo 同模型，scan 不引入独立 ACL）。
scan_id 路径校验：ScanStore.get_scan_dir 拒 ..//（防路径遍历）。

shim（api/workspaces.py 的 GET /{ws}、/{ws}/report|deliverables|logs、api/events.py 的
GET /{ws}/events、api/scan.py 的 DELETE /api/scan/{ws}）转发到 latest scan，供旧前端不破。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pathlib import Path

from supernova_web.auth.dependencies import current_user, workspace_member
from supernova_web.components.workspace_provisioner import is_global_admin
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
    """跨 ws 扫描聚合（IA 重设计 §3.1/§7.1）。canonical admin 见全部 ws 扫描，
    普通用户只见归属 ws（list_user_workspaces）的扫描。每条注入 workspace 字段，
    按 created_at 倒序。ws 量通常个位数到几十，每 ws list_scans 是目录扫描，可接受。"""
    from supernova_web.components.scan_store import ScanStore
    cfg = request.app.state.config
    indexer = request.app.state.indexer
    store = ScanStore(cfg.workspaces_dir)
    if is_global_admin(user):
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
    from supernova_web.components.scan_store import (
        resolve_workflow_id, _compute_progress_pct)
    mgr = SessionManager(scan_dir.parent)
    data = mgr.get_session_data(scan_dir)
    idx = request.app.state.indexer
    status = idx._status_of(scan_dir, mgr.get_status(scan_dir))
    combined = data.get("combined")
    bb_phase = data.get("bb_phase")
    return {
        "web_url": mgr.get_web_url(scan_dir),
        "repo_path": data.get("repo_path"),
        "scan_type": mgr.get_scan_type(scan_dir),
        "status": status,
        "created_at": _to_unix(mgr.get_created_at(scan_dir)),
        "completed_at": _to_unix(mgr.get_completed_at(scan_dir)),
        # 服务端墙钟基准（unix 秒）：前端 offset 校正用，消除跨时钟「总耗时负数」根因。
        "server_now": time.time(),
        "links": data.get("links", {}),
        "metrics": normalize_metrics(data.get("metrics", {})),
        "session": data.get("session", {}),
        "workflow_id": resolve_workflow_id(ws, scan_dir, scan_id),
        # 重跑预填用：白盒 repo 名 / 黑盒复用白盒 scan_id / 黑盒登录配置。
        "source_repo": data.get("source_repo"),
        "reuse_whitebox_scan_id": data.get("reuse_whitebox_scan_id"),
        "authentication": _read_auth_config(scan_dir),
        # 组合扫描字段 + 进度（spec §6.2/§9.2，2026-08-13 Task 1）：
        # combined/bb_phase/bb_reason 透传 session.json；progress_pct 三阶段加权预算；
        # expected_agents/completed_agents 是进度分母/分子（list_scans 已透传，详情一并给）。
        "combined": bool(combined) if combined is not None else None,
        "bb_phase": bb_phase,
        "bb_reason": data.get("bb_reason"),
        "progress_pct": _compute_progress_pct(status, combined, bb_phase, data),
        "expected_agents": data.get("expected_agents") or {},
        "completed_agents": data.get("completed_agents") or [],
    }


def _read_auth_config(scan_dir: Path) -> dict | None:
    """读 scan_dir/scan-config.yaml 的 authentication（黑盒登录配置，供重跑预填）。

    黑盒 _resolve_blackbox_inputs 在 req.authentication 非空时 dump 写入；无 auth 配置
    （黑盒未启用登录）/ 白盒（无该文件）-> None。损坏 YAML -> None（best-effort，不阻塞详情）。
    """
    cfg = scan_dir / "scan-config.yaml"
    if not cfg.exists():
        return None
    try:
        import yaml
        data = yaml.safe_load(cfg.read_text("utf-8"))
        if isinstance(data, dict) and isinstance(data.get("authentication"), dict):
            return data["authentication"]
    except (OSError, ValueError):
        return None
    return None


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


def report_for(scan_dir, track: str | None = None) -> str:
    """读 scan_dir 的综合报告 md（+ PoC 拼接）。

    track 解析：显式传入 > auto-infer（``DeliverablesReader._infer_track``，combined_report.md
    存在时优先 combined）。统一在 ``deliverables/{resolved}/`` 桶内挑报告（comprehensive 优先，
    否则首个 md）——不跨桶，跨桶 list_reports 会按错桶 read -> FileNotFoundError（regression）。
    PoC 拼接仅 whitebox 桶（PoC 集合是白盒产物；blackbox/combined 自含）。

    零回归：显式 track=None 时等价 auto-infer 单桶读——纯白盒/纯黑盒行为与旧 list_reports 一致
    （单桶时 comprehensive 挑选结果相同）。
    """
    from pathlib import Path
    reader = DeliverablesReader(scan_dir)
    resolved = reader._infer_track() if track is None else track
    track_dir = Path(scan_dir) / "deliverables" / resolved
    mds = sorted(f.name for f in track_dir.glob("*.md")) if track_dir.is_dir() else []
    chosen = next((x for x in mds if "comprehensive" in x.lower()), mds[0] if mds else None)
    if not chosen:
        return ""  # 该桶无报告产物 -> 200 空文本
    body = reader.read(chosen, resolved)
    if resolved == "whitebox":
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
async def scan_report(ws: str, scan_id: str, request: Request, _: User = Depends(workspace_member),
                      track: str | None = Query(None)):
    """综合报告（text/plain）。track 可选（spec §10.1 三视图）：whitebox/blackbox/combined
    取该桶报告；不传则 auto-infer（纯白盒/纯黑盒零回归）。"""
    return report_for(_scan_dir_or_404(request, ws, scan_id), track)


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


@router.post("/{ws}/scans/{scan_id}/combined/rerun-blackbox", status_code=202)
async def rerun_blackbox(ws: str, scan_id: str, request: Request,
                         _: User = Depends(workspace_member)):
    """组合扫描黑盒续跑（spec §11.3 / D5）：黑盒 failed 后换认证续跑，复用白盒产物，
    起新黑盒 workflow ``{ws}-{scan_id}-bb-rerun-{N}``。

    body 语义：空 / null / ``{}`` = 沿用原认证（v1 无新认证，前端 ``apiPost`` 恒发 JSON ``"{}"``）；
    非空 JSON 对象 = 换认证（须合法组合模式 ScanRequest：type=whitebox + url +
    authentication/auth_profile，复用既有 model 校验，非法 → 422）。

    手动读 raw body 而非 ``body: ScanRequest | None = Body(default=None)``——后者对
    ``Content-Type: application/json`` + ``{}`` 非空 body 强制按 ScanRequest 校验，type 必填 → 422，
    破坏 v1 无新认证路径（review-cea7ac6b..b4ece1b1 Important #1）。

    前置：scan 存在（404）+ combined 且 bb_phase=failed（422）+ 白盒产物完好（422）。
    换认证时先 _run_precheck 预验证——fail → 仍 202 但 bb_phase=auth_failed（异步标）。
    """
    import json as _json
    from pydantic import ValidationError
    from supernova_web.components.scan_manager import TemporalUnavailable
    from supernova_web.models import ScanRequest

    # 空 / null / {} = 沿用原认证；其余按 ScanRequest 校验（换认证）。
    new_auth = None
    raw = (await request.body()).strip()
    if raw and raw not in (b"null", b"{}", b"[]"):
        try:
            payload = _json.loads(raw)
        except _json.JSONDecodeError as e:
            raise HTTPException(422, f"invalid JSON body: {e}")
        if isinstance(payload, dict) and payload:
            try:
                new_auth = ScanRequest.model_validate(payload)
            except ValidationError as e:
                raise HTTPException(422, e.errors())

    sm = request.app.state.scan_manager
    try:
        await sm.rerun_blackbox(ws, scan_id, new_auth=new_auth)
    except ValueError as e:
        msg = str(e)
        if "不存在" in msg:
            raise HTTPException(404, msg)
        raise HTTPException(422, msg)
    except TemporalUnavailable:
        raise HTTPException(400, "Temporal 服务未运行，请先 docker-compose up -d")
    return {"workspace": ws, "scan_id": scan_id}
