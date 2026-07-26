from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class AuthMiddleware(BaseHTTPMiddleware):
    """读 sn-sid cookie → session_manager.verify → 注入 request.state.user。
    不阻断；路由用 Depends(current_user) 决定是否要求登录。"""

    async def dispatch(self, request: Request, call_next):
        sm = getattr(request.app.state, "session_manager", None)
        user = None
        if sm is not None:
            sid = request.cookies.get("sn-sid")
            if sid:
                user = sm.verify(sid)
        request.state.user = user
        return await call_next(request)
