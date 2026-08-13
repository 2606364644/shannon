from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from .models import User
from supernova_web.components.workspace_provisioner import is_global_admin, is_safe_workspace_name


def current_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


def require_admin(request: Request) -> User:
    user = current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin required")
    return user


def workspace_member(request: Request, ws: str, user: User = Depends(current_user)) -> User:
    if not is_safe_workspace_name(ws):
        raise HTTPException(status_code=422, detail="invalid workspace name")
    if is_global_admin(user):
        return user
    if request.app.state.auth_store.get_workspace_member_role(ws, user.id) is None:
        raise HTTPException(status_code=403, detail="not a workspace member")
    return user


def workspace_manager(request: Request, ws: str, user: User = Depends(current_user)) -> User:
    if not is_safe_workspace_name(ws):
        raise HTTPException(status_code=422, detail="invalid workspace name")
    if is_global_admin(user):
        return user
    if request.app.state.auth_store.get_workspace_member_role(ws, user.id) != "manager":
        raise HTTPException(status_code=403, detail="workspace manager required")
    return user
