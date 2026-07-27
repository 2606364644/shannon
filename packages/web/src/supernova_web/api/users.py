# packages/web/src/supernova_web/api/users.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Literal

from supernova_web.auth.csrf import verify_csrf
from supernova_web.auth.dependencies import current_user, require_admin
from supernova_web.auth.models import User
from supernova_web.auth.passwords import NEW_PASSWORD_MIN_LEN, hash_password

router = APIRouter(prefix="/api/users", tags=["users"])


class PinnedWorkspaceIn(BaseModel):
    workspace: str


@router.put("/me/pinned-workspace")
async def set_pinned_workspace(body: PinnedWorkspaceIn, request: Request,
                               user: User = Depends(current_user)):
    """per-user 置顶工作区（IA 重设计 §2.3）。只能 pin 有权限的 ws
    （workspace_member 依赖项鉴权：admin 全部、普通用户需为成员）。"""
    _check_csrf(request)
    # workspace_member 依赖项的 ws 来自路径参数，此处 ws 来自 body，手动复用同款鉴权。
    # 顺序：先 403（成员检查）后 404（存在性）--非成员对任意 ws 一律 403，不泄露 ws 存在性，
    # 与 workspace_member 依赖项语义一致；admin 跳过 403 后命中 404。get_workspace_member_role
    # 对不存在的 ws 返 None -> 403，故非成员探测不到存在性。
    if user.role != "admin":
        role = request.app.state.auth_store.get_workspace_member_role(body.workspace, user.id)
        if role is None:
            raise HTTPException(403, "not a workspace member")
    ws_dir = request.app.state.config.workspaces_dir / body.workspace
    if not ws_dir.exists():
        raise HTTPException(404, "workspace not found")
    request.app.state.auth_store.update_pinned_workspace(user.id, body.workspace)
    return {"pinned": body.workspace}


class CreateUserIn(BaseModel):
    username: str
    password: str
    role: Literal["admin", "user"] = "user"


class UpdateRoleIn(BaseModel):
    role: Literal["admin", "user"]


class ResetPasswordIn(BaseModel):
    new_password: str


def _user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role,
            "must_change_password": u.must_change_password, "created_at": u.created_at}


def _check_csrf(request: Request) -> None:
    if not verify_csrf(request.headers.get("x-csrf-token"), request.cookies.get("sn-csrf")):
        raise HTTPException(status_code=403, detail="invalid csrf token")


def _admin_count(store) -> int:
    return sum(1 for u in store.list_all_users() if u.role == "admin")


@router.get("")
async def list_users(request: Request, _: User = Depends(require_admin)):
    store = request.app.state.auth_store
    return {"users": [_user_out(u) for u in store.list_all_users()]}


@router.post("")
async def create_user(body: CreateUserIn, request: Request, _: User = Depends(require_admin)):
    _check_csrf(request)
    if len(body.password) < NEW_PASSWORD_MIN_LEN:
        raise HTTPException(400, f"password must be at least {NEW_PASSWORD_MIN_LEN} characters")
    store = request.app.state.auth_store
    if store.get_user_by_username(body.username) is not None:
        raise HTTPException(409, "username exists")
    u = store.create_user(body.username, hash_password(body.password),
                          role=body.role, must_change=True)
    return {"user": _user_out(u)}


@router.delete("/{user_id}")
async def delete_user(user_id: int, request: Request, admin: User = Depends(require_admin)):
    _check_csrf(request)
    store = request.app.state.auth_store
    target = store.get_user(user_id)
    if target is None:
        raise HTTPException(404, "user not found")
    if target.id == admin.id:
        raise HTTPException(409, "cannot delete self")
    if target.role == "admin" and _admin_count(store) <= 1:
        raise HTTPException(409, "cannot delete last admin")
    store.delete_user(user_id)
    return {"ok": True}


@router.patch("/{user_id}")
async def update_role(user_id: int, body: UpdateRoleIn, request: Request,
                      admin: User = Depends(require_admin)):
    _check_csrf(request)
    store = request.app.state.auth_store
    target = store.get_user(user_id)
    if target is None:
        raise HTTPException(404, "user not found")
    if target.id == admin.id and body.role != "admin":
        raise HTTPException(409, "cannot demote self")
    if target.role == "admin" and body.role != "admin" and _admin_count(store) <= 1:
        raise HTTPException(409, "cannot demote last admin")
    store.update_role(user_id, body.role)
    return {"ok": True}


@router.post("/{user_id}/reset-password")
async def reset_password(user_id: int, body: ResetPasswordIn, request: Request,
                         _: User = Depends(require_admin)):
    _check_csrf(request)
    if len(body.new_password) < NEW_PASSWORD_MIN_LEN:
        raise HTTPException(400, f"password must be at least {NEW_PASSWORD_MIN_LEN} characters")
    store = request.app.state.auth_store
    if store.get_user(user_id) is None:
        raise HTTPException(404, "user not found")
    store.reset_password(user_id, hash_password(body.new_password))
    return {"ok": True}


@router.get("/{user_id}/workspaces")
async def user_workspaces(user_id: int, request: Request, _: User = Depends(require_admin)):
    store = request.app.state.auth_store
    if store.get_user(user_id) is None:
        raise HTTPException(404, "user not found")
    pairs = store.list_user_workspaces_with_role(user_id)
    return {"workspaces": [{"workspace": w, "role": r} for w, r in pairs]}
