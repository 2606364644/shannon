# packages/web/src/supernova_web/api/members.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Literal

from supernova_web.auth.csrf import verify_csrf
from supernova_web.auth.dependencies import workspace_member, workspace_manager
from supernova_web.auth.models import User

router = APIRouter(prefix="/api/workspaces", tags=["members"])


class AddMemberIn(BaseModel):
    username: str
    role: str | None = "member"


@router.get("/{ws}/members")
async def list_members(ws: str, request: Request, _: User = Depends(workspace_member)):
    members = request.app.state.auth_store.list_workspace_members(ws)
    return {"members": [{"user_id": uid, "username": un, "role": r} for uid, un, r in members]}


@router.post("/{ws}/members")
async def add_member(ws: str, body: AddMemberIn, request: Request, _: User = Depends(workspace_manager)):
    target = request.app.state.auth_store.get_user_by_username(body.username)
    if target is None:
        raise HTTPException(404, "user not found")
    role = body.role if body.role in ("manager", "member") else "member"
    request.app.state.auth_store.add_workspace_member(ws, target.id, role)
    return {"ok": True}


@router.delete("/{ws}/members/{username}")
async def remove_member(ws: str, username: str, request: Request, _: User = Depends(workspace_manager)):
    target = request.app.state.auth_store.get_user_by_username(username)
    if target is None:
        raise HTTPException(404, "user not found")
    members = request.app.state.auth_store.list_workspace_members(ws)
    managers = [m for m in members if m[2] == "manager"]
    if len(managers) <= 1 and any(m[1] == username for m in managers):
        raise HTTPException(409, "不能移除最后一个 manager")
    request.app.state.auth_store.remove_workspace_member(ws, target.id)
    return {"ok": True}


def _check_csrf_members(request: Request) -> None:
    if not verify_csrf(request.headers.get("x-csrf-token"), request.cookies.get("sn-csrf")):
        raise HTTPException(status_code=403, detail="invalid csrf token")


class UpdateMemberRoleIn(BaseModel):
    role: Literal["manager", "member"]


@router.patch("/{ws}/members/{username}")
async def update_member_role(ws: str, username: str, body: UpdateMemberRoleIn,
                             request: Request, _: User = Depends(workspace_manager)):
    _check_csrf_members(request)
    store = request.app.state.auth_store
    target = store.get_user_by_username(username)
    if target is None:
        raise HTTPException(404, "user not found")
    members = store.list_workspace_members(ws)
    if not any(m[1] == username for m in members):
        raise HTTPException(404, "not a member")
    # 护栏：降最后 manager -> 拒（与 remove_member 同逻辑）
    managers = [m for m in members if m[2] == "manager"]
    if body.role != "manager" and len(managers) <= 1 and any(m[1] == username for m in managers):
        raise HTTPException(409, "不能降最后一个 manager")
    store.update_workspace_member_role(ws, target.id, body.role)
    return {"ok": True}
