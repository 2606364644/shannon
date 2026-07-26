from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from .models import User


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
    if user.role == "admin":
        return user
    if request.app.state.auth_store.get_workspace_member_role(ws, user.id) is None:
        raise HTTPException(status_code=403, detail="not a workspace member")
    return user


def workspace_manager(request: Request, ws: str, user: User = Depends(current_user)) -> User:
    if user.role == "admin":
        return user
    if request.app.state.auth_store.get_workspace_member_role(ws, user.id) != "manager":
        raise HTTPException(status_code=403, detail="workspace manager required")
    return user
