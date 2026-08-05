"""认证档案 CRUD API(workspace 级,脱敏读写,空串 secret = 不改)。

test/verify-status 端点在本文件追加(Task 6)。鉴权:看/用 workspace_member,改/删 workspace_manager。
范式镜像 api/ws_config.py。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from supernova_web.auth.dependencies import workspace_member, workspace_manager
from supernova_web.components.auth_profile_store import (
    AuthProfileStore, AuthProfile, AuthProfileCredential, EmailLoginCred,
    _CRED_SECRET_FIELDS,
)

router = APIRouter(prefix="/api/workspaces", tags=["auth-profiles"])


def _store(request: Request) -> AuthProfileStore:
    return request.app.state.auth_profile_store


def _build_profile(payload: dict) -> AuthProfile:
    creds = [AuthProfileCredential(**{"id": "", **c}) for c in payload.get("credentials", [])]
    return AuthProfile(
        id=payload.get("id", ""),
        name=payload["name"],
        login_url=payload["login_url"],
        login_type=payload["login_type"],
        login_flow=payload.get("login_flow"),
        credentials=creds,
    )


@router.get("/{ws}/auth-profiles")
async def list_profiles(ws: str, request: Request, user=Depends(workspace_member)):
    return [p.model_dump(mode="json") for p in _store(request).read_masked(ws)]


@router.post("/{ws}/auth-profiles")
async def create_profile(ws: str, payload: dict, request: Request,
                         user=Depends(workspace_manager)):
    store = _store(request)
    # 唯一性:ws 内 name 唯一
    if any(p.name == payload.get("name") for p in store.read(ws)):
        raise HTTPException(422, f"档案名已存在: {payload.get('name')}")
    profile = store.upsert_profile(ws, _build_profile(payload))
    return next(m for m in store.read_masked(ws) if m.id == profile.id).model_dump(mode="json")


@router.get("/{ws}/auth-profiles/{pid}")
async def get_profile(ws: str, pid: str, request: Request, user=Depends(workspace_member)):
    p = _store(request).get(ws, pid)
    if p is None:
        raise HTTPException(404, "认证档案不存在")
    # 脱敏:取 read_masked 中匹配
    masked = next((m for m in _store(request).read_masked(ws) if m.id == pid), None)
    return masked.model_dump(mode="json")


@router.put("/{ws}/auth-profiles/{pid}")
async def update_profile(ws: str, pid: str, payload: dict, request: Request,
                         user=Depends(workspace_manager)):
    store = _store(request)
    existing = store.get(ws, pid)
    if existing is None:
        raise HTTPException(404, "认证档案不存在")
    # profile 级字段覆盖
    existing.name = payload.get("name", existing.name)
    existing.login_url = payload.get("login_url", existing.login_url)
    existing.login_type = payload.get("login_type", existing.login_type)
    existing.login_flow = payload.get("login_flow", existing.login_flow)
    # credentials:逐条 upsert,空串 secret = 保留原值
    existing_by_id = {c.id: c for c in existing.credentials}
    for c_in in payload.get("credentials", []):
        cid = c_in.get("id")
        if cid and cid in existing_by_id:  # 更新现有
            c = existing_by_id[cid]
            c.role = c_in.get("role", c.role)
            c.username = c_in.get("username", c.username)
            for f in _CRED_SECRET_FIELDS:
                v = c_in.get(f, "")
                if v:
                    setattr(c, f, v)   # 非空 → 更新;空串 → 保留
            if c_in.get("email_login"):
                el = c.email_login or EmailLoginCred(address="", password=None)
                el.address = c_in["email_login"].get("address", el.address)
                for f in _CRED_SECRET_FIELDS:
                    v = c_in["email_login"].get(f, "")
                    if v:
                        setattr(el, f, v)
                c.email_login = el
        else:  # 新增 credential
            existing.credentials.append(AuthProfileCredential(**{"id": "", **{k: v for k, v in c_in.items() if k != "id"}}))
    store.upsert_profile(ws, existing)
    return {"ok": True}


@router.delete("/{ws}/auth-profiles/{pid}")
async def delete_profile(ws: str, pid: str, request: Request,
                         user=Depends(workspace_manager)):
    if not _store(request).delete_profile(ws, pid):
        raise HTTPException(404, "认证档案不存在")
    return {"ok": True}


@router.post("/{ws}/auth-profiles/{pid}/credentials/{cid}/test")
async def test_credential(ws: str, pid: str, cid: str, request: Request,
                          user=Depends(workspace_member)):
    """触发真实登录验证 → 起 AuthValidationWorkflow,返 {workflow_id, probe_dir}(前端轮询)。"""
    return await request.app.state.scan_manager.start_auth_validation(ws, pid, cid)


@router.get("/{ws}/auth-profiles/{pid}/credentials/{cid}/verify-status")
async def verify_status(ws: str, pid: str, cid: str, workflow_id: str,
                        probe_dir: str, request: Request,
                        user=Depends(workspace_member)):
    """轮询验证结果 → 回填 verify_status → 删 probe 目录。Temporal 未就绪抛错前端提示重试。"""
    try:
        status = await request.app.state.scan_manager.get_auth_validation_result(
            ws, workflow_id, probe_dir, pid, cid)
    except Exception as e:
        raise HTTPException(503, f"验证结果暂时不可用,请重试: {e}")
    return status.model_dump(mode="json")
