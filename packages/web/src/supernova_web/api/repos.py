from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from supernova_web.auth.dependencies import require_admin, workspace_member
from supernova_web.auth.models import User
from supernova_web.components.event_tailer import EventTailer
from supernova_web.components.repo_manager import (
    TooManyClones,
    UploadTooLarge,
    _validate_repo_name,
    _validate_repo_segment,
)

router = APIRouter(prefix="/api/workspaces", tags=["repos"])


class CreateRepoBody(BaseModel):
    git_url: str
    branch: str | None = None
    commit: str | None = None
    name: str | None = None
    group: str | None = None


class LinkDirBody(BaseModel):
    path: str


class CheckoutBody(BaseModel):
    branch: str


@router.get("/{ws}/repos")
async def list_repos(ws: str, request: Request, _: User = Depends(workspace_member)):
    return request.app.state.repo_manager.list_repos(ws)


@router.post("/{ws}/repos", status_code=202)
async def create_repo(ws: str, body: CreateRepoBody, request: Request,
                      _: User = Depends(workspace_member)):
    rm = request.app.state.repo_manager
    try:
        name = await rm.clone(ws, body.git_url, body.branch, body.commit, body.name, body.group)
    except PermissionError:
        raise HTTPException(503, "未配置 git 凭据（GITLAB_USER/TOKEN）")
    except ValueError as e:        # 已存在
        raise HTTPException(409, str(e))
    except TooManyClones as e:
        raise HTTPException(409, f"并发 clone 上限 {e.limit}")
    return {"name": name}


@router.post("/{ws}/repos/link-dir", status_code=200)
async def link_repos_in_dir(ws: str, body: LinkDirBody, request: Request,
                            _: User = Depends(require_admin)):
    """批量关联父目录下的所有 git 仓库（admin-only）。返回 {imported, skipped}。"""
    rm = request.app.state.repo_manager
    try:
        return rm.link_repos_in_dir(ws, body.path)
    except ValueError as e:  # 路径不存在 / 非目录
        raise HTTPException(422, str(e))


# multipart 分块落盘大小（UploadFile 直接 read() 会整包进内存，1GB 上限下不可接受）
_UPLOAD_CHUNK = 1024 * 1024


@router.post("/{ws}/repos/upload", status_code=202)
async def upload_repo_zip(ws: str, request: Request,
                          file: UploadFile = File(...),
                          name: str | None = Form(None),
                          group: str | None = Form(None),
                          _: User = Depends(workspace_member)):
    """上传 zip 添加仓库（所有成员，与 clone 一致）：分块落盘临时文件 → RepoManager
    后台解压 + 快照化（防 zip slip/bomb；无 .git 则 git init 单 commit）。返回 {name}。

    临时 zip 落 repos 根下隐藏文件（``.upload-incoming-*.zip``，与解压目标同文件系统）；
    所有权移交：upload_zip 启动后台 task 成功 → task 结束时由组件删除（API 不删——
    202 返回时解压尚未开始，此刻删会让后台解压读到半截/缺失文件）；upload_zip 抛异常
    （校验失败，task 未启动）→ API finally 删。
    """
    from supernova_web.config import get_config
    limit = get_config().max_upload_zip_bytes
    # name/group 预校验（422）：rm.upload_zip 内 segment 校验是二道防线
    for seg, label in ((name, "名字段"), (group, "分组名")):
        if seg:
            try:
                _validate_repo_segment(seg, label)
            except ValueError as e:
                raise HTTPException(422, str(e))
    rm = request.app.state.repo_manager
    # 分块落盘：超限即刻中止（413），不让超大包占满内存/磁盘
    repos_root = rm._repos_root(ws)
    repos_root.mkdir(parents=True, exist_ok=True)
    zip_path = repos_root / f".upload-incoming-{uuid4().hex[:12]}.zip"
    fd = open(zip_path, "wb")
    try:
        total = 0
        while chunk := await file.read(_UPLOAD_CHUNK):
            total += len(chunk)
            if total > limit:
                raise HTTPException(413, f"zip 超过大小上限（{limit // (1024*1024)} MB）")
            fd.write(chunk)
        fd.close()
        try:
            final = await rm.upload_zip(ws, zip_path, file.filename or "upload.zip",
                                        name, group)
        except UploadTooLarge as e:
            raise HTTPException(413, f"zip 超过大小上限（{e.limit // (1024*1024)} MB）")
        except ValueError as e:  # 非 .zip 扩展 / 仓库名已存在
            raise HTTPException(409, str(e))
        except TooManyClones as e:
            raise HTTPException(409, f"并发任务上限 {e.limit}")
    except BaseException:
        zip_path.unlink(missing_ok=True)  # task 未启动（upload_zip 抛了）→ API 兜底删
        raise
    return {"name": final}


# 批量删除/取消关联单次上限（防超大请求体遍历过久）
BATCH_DELETE_MAX_NAMES = 200


class BatchDeleteBody(BaseModel):
    names: list[str]


@router.post("/{ws}/repos/batch-delete", status_code=200)
async def batch_delete_repos(ws: str, body: BatchDeleteBody, request: Request,
                             _: User = Depends(workspace_member)):
    """批量删除/取消关联：私有克隆→rmtree，关联仓→unlink（不删源文件）。
    部分被在跑 scan 引用 / clone-pull 忙碌 / 不存在 → 跳过并收集 skipped（对齐 link-dir）。
    返回 ``{"deleted": [...], "unlinked": [...], "skipped": [{"name","reason"}]}``。"""
    # 去重（保序）
    seen: set[str] = set()
    names: list[str] = []
    for n in body.names:
        if n not in seen:
            seen.add(n)
            names.append(n)
    if not names:
        raise HTTPException(422, "names 不能为空")
    if len(names) > BATCH_DELETE_MAX_NAMES:
        raise HTTPException(422, f"单次最多 {BATCH_DELETE_MAX_NAMES} 个仓库")
    # 路径穿越防线：逐项校验，任一非法 → 422 拒整批（恶意 name 不得混入处理流）
    for n in names:
        try:
            _validate_repo_name(n)
        except ValueError:
            raise HTTPException(422, f"非法仓库名：{n!r}")

    rm = request.app.state.repo_manager
    sm = request.app.state.scan_manager
    busy_sources = sm.active_repo_sources()  # 一次性快照（避免分次检查窗口）

    deleted: list[str] = []
    unlinked: list[str] = []
    skipped: list[dict] = []
    for name in names:
        if (ws, name) in busy_sources:
            skipped.append({"name": name, "reason": "scanning"})
            continue
        outcome = await rm.delete_one(ws, name)
        if outcome == "deleted":
            deleted.append(name)
        elif outcome == "unlinked":
            unlinked.append(name)
        else:  # busy / not_found
            skipped.append({"name": name, "reason": outcome})
    return {"deleted": deleted, "unlinked": unlinked, "skipped": skipped}


# 仓库名可为 group/repo（含 '/'），故用 {name:path} 吃整段路径。带后缀的具体路由
# （events/pull/checkout）必须声明在 GET /{name:path} 之前——{name:path} 贪婪匹配
# 否则会吞掉后缀（/api/workspaces/{ws}/repos/foo/events 被当 name="foo/events"）。
# 先声明的先匹配。
@router.get("/{ws}/repos/{name:path}/events")
async def repo_events(ws: str, name: str, request: Request,
                      _: User = Depends(workspace_member)):
    rm = request.app.state.repo_manager
    if rm.get_repo(ws, name) is None:
        raise HTTPException(404, "repo not found")
    # 关联仓库无 clone.ndjson（非 clone 产物）→ 空流（200），不 tail 不存在的文件
    if rm._is_linked(ws, name):
        return StreamingResponse(iter(()), media_type="text/event-stream")
    ndjson = rm._repo_dir(ws, name) / "clone.ndjson"
    last = request.headers.get("Last-Event-ID")
    last_offset = int(last) if last else None

    async def gen():
        tailer = EventTailer(ndjson)
        # EventTailer.tail 用 callback 而非 async generator；用 queue 桥接成 SSE
        queue: asyncio.Queue = asyncio.Queue()
        SENTINEL = object()

        async def cb(data, offset):
            await queue.put(EventTailer.encode_sse(data, event_id=offset))

        async def run_tail():
            await tailer.tail(cb, last_event_id=last_offset, stop_type="clone_end")
            await queue.put(SENTINEL)

        task = asyncio.create_task(run_tail())
        try:
            while True:
                item = await queue.get()
                if item is SENTINEL:
                    break
                yield item
        finally:
            task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/{ws}/repos/{name:path}/branches")
async def list_repo_branches(ws: str, name: str, request: Request,
                             _: User = Depends(workspace_member)):
    """列远端分支（分支列 combobox 数据源，ls-remote --heads 问远端不依赖本地 ref）。

    错误分档：仓库级 404/405/409（对齐 pull/checkout）；ls-remote 是网络调用，
    失败/超时 → 502（前端降级为手输分支名），区别于服务器错误 500。
    声明须在 GET /{name:path} 之前——{name:path} 贪婪匹配会吞掉 /branches 后缀。
    """
    rm = request.app.state.repo_manager
    if rm.get_repo(ws, name) is None:
        raise HTTPException(404, "repo not found")
    if rm._is_linked(ws, name):
        raise HTTPException(405, "关联仓库为共享路径，不可在此修改")
    if rm._is_upload(ws, name):
        raise HTTPException(405, "上传仓库为静态快照，无远端分支")
    try:
        branches = await rm.list_branches(ws, name)
    except ValueError as e:        # clone/pull 忙碌
        raise HTTPException(409, str(e))
    except RuntimeError as e:      # ls-remote 失败/超时（网络/凭据失效）
        raise HTTPException(502, str(e))
    return {"branches": branches}


@router.get("/{ws}/repos/{name:path}")
async def get_repo(ws: str, name: str, request: Request,
                   _: User = Depends(workspace_member)):
    repo = request.app.state.repo_manager.get_repo(ws, name)
    if repo is None:
        raise HTTPException(404, "repo not found")
    return repo


@router.delete("/{ws}/repos/{name:path}")
async def delete_repo(ws: str, name: str, request: Request,
                      _: User = Depends(workspace_member)):
    rm = request.app.state.repo_manager
    sm = request.app.state.scan_manager
    # T2 期间 active_repo_sources() 仍返 set[str]（仅 name），(ws, name) in 永为 False；
    # T3 把返回类型改为 set[tuple[str, str]] 后此门才生效（latent no-op, 见 plan）。
    if (ws, name) in sm.active_repo_sources():
        raise HTTPException(409, "仓库正被扫描引用")
    try:
        await rm.delete(ws, name)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"deleted": name}


@router.post("/{ws}/repos/{name:path}/pull", status_code=202)
async def pull_repo(ws: str, name: str, request: Request,
                    _: User = Depends(workspace_member)):
    rm = request.app.state.repo_manager
    if rm._is_linked(ws, name):
        raise HTTPException(405, "关联仓库为共享路径，不可在此修改")
    if rm._is_upload(ws, name):
        raise HTTPException(405, "上传仓库为静态快照，不可 pull（请重新上传更新）")
    try:
        await rm.pull(ws, name)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"pulling": name}


@router.post("/{ws}/repos/{name:path}/checkout")
async def checkout_repo(ws: str, name: str, body: CheckoutBody, request: Request,
                        _: User = Depends(workspace_member)):
    rm = request.app.state.repo_manager
    sm = request.app.state.scan_manager
    if rm._is_linked(ws, name):
        raise HTTPException(405, "关联仓库为共享路径，不可在此修改")
    if rm._is_upload(ws, name):
        raise HTTPException(405, "上传仓库为静态快照，不可切换分支（请重新上传更新）")
    # 扫描 worker 直读仓库工作树（共享 volume）：运行中切换会让 worker 读到混合
    # 分支代码 → 与 delete 同款引用锁拒绝（spec 2026-08-21 §2b）。
    if (ws, name) in sm.active_repo_sources():
        raise HTTPException(409, "仓库正被扫描引用")
    try:
        await rm.checkout(ws, name, body.branch)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"checked_out": body.branch}
