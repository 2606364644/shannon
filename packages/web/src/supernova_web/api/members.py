# packages/web/src/supernova_web/api/members.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

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
