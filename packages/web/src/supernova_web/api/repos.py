from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from supernova_web.components.event_tailer import EventTailer
from supernova_web.components.repo_manager import TooManyClones

router = APIRouter(prefix="/api/repos", tags=["repos"])


class CreateRepoBody(BaseModel):
    git_url: str
    branch: str | None = None
    commit: str | None = None
    name: str | None = None
    group: str | None = None


class CheckoutBody(BaseModel):
    branch: str


@router.get("")
async def list_repos(request: Request):
    return request.app.state.repo_manager.list_repos()


@router.post("", status_code=202)
async def create_repo(body: CreateRepoBody, request: Request):
    rm = request.app.state.repo_manager
    try:
        name = await rm.clone(body.git_url, body.branch, body.commit, body.name, body.group)
    except PermissionError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:        # 已存在
        raise HTTPException(409, str(e))
    except TooManyClones as e:
        raise HTTPException(409, f"并发 clone 上限 {e.limit}")
    return {"name": name}


# 仓库名可为 group/repo（含 '/'），故用 {name:path} 吃整段路径。带后缀的具体路由
# （events/pull/checkout）必须声明在 GET /{name:path} 之前——{name:path} 贪婪匹配
# 否则会吞掉后缀（/api/repos/foo/events 被当 name="foo/events"）。先声明的先匹配。
@router.get("/{name:path}/events")
async def repo_events(name: str, request: Request):
    rm = request.app.state.repo_manager
    if rm.get_repo(name) is None:
        raise HTTPException(404, "repo not found")
    ndjson = rm._repo_dir(name) / "clone.ndjson"
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


@router.get("/{name:path}")
async def get_repo(name: str, request: Request):
    repo = request.app.state.repo_manager.get_repo(name)
    if repo is None:
        raise HTTPException(404, "repo not found")
    return repo


@router.delete("/{name:path}")
async def delete_repo(name: str, request: Request):
    rm = request.app.state.repo_manager
    sm = request.app.state.scan_manager
    if name in sm.active_repo_sources():
        raise HTTPException(409, "仓库正被扫描引用")
    try:
        await rm.delete(name)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"deleted": name}


@router.post("/{name:path}/pull", status_code=202)
async def pull_repo(name: str, request: Request):
    rm = request.app.state.repo_manager
    try:
        await rm.pull(name)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"pulling": name}


@router.post("/{name:path}/checkout")
async def checkout_repo(name: str, body: CheckoutBody, request: Request):
    rm = request.app.state.repo_manager
    try:
        await rm.checkout(name, body.branch)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"checked_out": body.branch}
