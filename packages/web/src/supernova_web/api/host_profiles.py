"""HOST 档案 CRUD API(workspace 级,明文不脱敏,无 verify/test 端点)。

范式镜像 ``api/auth_profiles.py`` 但更简:HostProfile 只有 ``mappings``
(list[HostMapping])——**无 credentials / 无加密 / 无脱敏 / 无 verify/test 端点**。

鉴权:看(GET/list)用 workspace_member,改/删(POST/PUT/DELETE/fork/parse/refresh)
用 workspace_manager。system-scope 档案 PUT/DELETE → 403(只读,fork only)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from supernova_web.auth.dependencies import workspace_member, workspace_manager
from supernova_web.components.host_profile_store import (
    AlreadyForked,
    HostMapping,
    HostProfile,
    HostProfileRefreshEmpty,
    HostProfileStore,
    fetch_and_parse_hosts,
)

router = APIRouter(prefix="/api/workspaces", tags=["host-profiles"])


def _store(request: Request) -> HostProfileStore:
    return request.app.state.host_profile_store


def _build_profile(payload: dict) -> HostProfile:
    """payload → HostProfile(显式构造,mappings 用 HostMapping 强制规范化)。"""
    return HostProfile(
        id=payload.get("id", ""),
        name=payload["name"],
        source_url=payload.get("source_url"),
        mappings=[HostMapping(**m) for m in payload.get("mappings", [])],
    )


@router.get("/{ws}/host-profiles")
async def list_profiles(ws: str, request: Request,
                        user=Depends(workspace_member)):
    """列出 ws 段 + .system 段合并后的全部 host 档案(ws 段优先)。"""
    return [p.model_dump(mode="json") for p in _store(request).read(ws)]


@router.post("/{ws}/host-profiles")
async def create_profile(ws: str, payload: dict, request: Request,
                         user=Depends(workspace_manager)):
    store = _store(request)
    # 唯一性:ws 内 name 唯一(不含 .system 段,只看 ws 自有)
    if any(p.name == payload.get("name") for p in store._read_segment(ws)):
        raise HTTPException(422, f"档案名已存在: {payload.get('name')}")
    try:
        profile = store.upsert_profile(ws, _build_profile(payload))
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(422, f"HOST 档案映射无效: {exc}") from exc
    return profile.model_dump(mode="json")


# 重要:静态路径 /parse 必须在 /{pid} 路由之前定义,否则 "parse" 会被当 pid 匹配。
@router.post("/{ws}/host-profiles/parse")
async def parse_profile(ws: str, url: str, request: Request,
                        user=Depends(workspace_manager)):
    """GET + 解析 /etc/hosts,返回 {mappings, warnings} —— **不落盘**(预览)。

    fetch_and_parse_hosts 任何异常(网络 / 解析)→ 422。
    """
    try:
        mappings, warnings = await fetch_and_parse_hosts(url)
    except Exception as e:  # noqa: BLE001 —— 预览端点:任何失败都报 422 给前端
        raise HTTPException(422, f"拉取/解析失败: {e}")
    return {
        "mappings": [m.model_dump(mode="json") for m in mappings],
        "warnings": warnings,
    }


@router.get("/{ws}/host-profiles/{pid}")
async def get_profile(ws: str, pid: str, request: Request,
                      user=Depends(workspace_member)):
    p = _store(request).get(ws, pid)
    if p is None:
        raise HTTPException(404, "HOST 档案不存在")
    return p.model_dump(mode="json")


@router.put("/{ws}/host-profiles/{pid}")
async def update_profile(ws: str, pid: str, payload: dict, request: Request,
                         user=Depends(workspace_manager)):
    store = _store(request)
    existing = store.get(ws, pid)
    if existing is None:
        raise HTTPException(404, "HOST 档案不存在")
    if existing.scope == "system":
        raise HTTPException(403, "系统档案只读,请修改 configs 文件后重启")
    # 字段覆盖(局部更新兼容:payload 缺省 → 保留原值)
    try:
        candidate = existing.model_dump(mode="json")
        candidate["name"] = payload.get("name", existing.name)
        candidate["source_url"] = payload.get("source_url", existing.source_url)
        if "mappings" in payload:
            candidate["mappings"] = payload["mappings"]
        validated = HostProfile.model_validate(candidate)
        store.upsert_profile(ws, validated)
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(422, f"HOST 档案映射无效: {exc}") from exc
    return {"ok": True}


@router.delete("/{ws}/host-profiles/{pid}")
async def delete_profile(ws: str, pid: str, request: Request,
                         user=Depends(workspace_manager)):
    store = _store(request)
    existing = store.get(ws, pid)
    if existing is None:
        raise HTTPException(404, "HOST 档案不存在")
    if existing.scope == "system":
        raise HTTPException(403, "系统档案只读,请修改 configs 文件后重启")
    if not store.delete_profile(ws, pid):
        raise HTTPException(404, "HOST 档案不存在")
    return {"ok": True}


@router.post("/{ws}/host-profiles/{pid}/fork")
async def fork_profile(ws: str, pid: str, request: Request,
                       user=Depends(workspace_manager)):
    """把系统档案 fork 成本工作区可编辑副本(保留 profile.id,ws-priority 覆盖)。

    - AlreadyForked → 409(ws 段已有同 id 副本)
    - None → 422(ws 段自有该档案,可直接编辑)/ 404(系统段也无)
    """
    store = _store(request)
    try:
        forked = store.fork_from_system(ws, pid)
    except AlreadyForked:
        raise HTTPException(409, "已复制到本工作区")
    if forked is None:
        if store.get(ws, pid) is not None:
            raise HTTPException(422, "该档案已在工作区,可直接编辑")
        raise HTTPException(404, "HOST 档案不存在")
    return forked.model_dump(mode="json")


@router.post("/{ws}/host-profiles/{pid}/refresh")
async def refresh_profile(ws: str, pid: str, request: Request,
                          user=Depends(workspace_manager)):
    """按 profile.source_url 重新拉取 → 更新 mappings + 落盘(best-effort,失败保留快照)。

    系统档案(scope=system)只读:refresh 内部调 upsert_profile(ws, profile) 会用
    系统 profile.id 写到 ws 段,致 ws-priority 阴影系统原型 + 后续 fork 误判
    AlreadyForked —— 故 system 档案拒(必须 fork 后刷新副本),与 PUT/DELETE 同语义。
    """
    store = _store(request)
    existing = store.get(ws, pid)
    if existing is None:
        raise HTTPException(404, "HOST 档案不存在")
    if existing.scope == "system":
        raise HTTPException(403, "系统档案只读,请 fork 后刷新副本")
    try:
        refreshed = await store.refresh(ws, pid)
    except HostProfileRefreshEmpty as exc:
        raise HTTPException(422, f"HOST 刷新后没有有效 mapping: {exc}") from exc
    return refreshed.model_dump(mode="json")
