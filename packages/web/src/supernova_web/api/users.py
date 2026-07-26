# packages/web/src/supernova_web/api/users.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from supernova_web.auth.dependencies import current_user
from supernova_web.auth.models import User

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("")
async def list_users(request: Request, _: User = Depends(current_user)):
    return {"users": [{"id": u.id, "username": u.username, "role": u.role}
                      for u in request.app.state.auth_store.list_all_users()]}
