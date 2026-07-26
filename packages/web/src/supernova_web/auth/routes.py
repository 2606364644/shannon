from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .brute import BruteGuard
from .csrf import generate_csrf_token, verify_csrf
from .dependencies import current_user
from .models import User
from .passwords import verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

_brute = BruteGuard()


class LoginIn(BaseModel):
    username: str
    password: str


def _user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role}


def _cookie_secure(cfg, request: Request) -> bool:
    """sn-sid/sn-csrf 是否打 Secure 标志。

    - cfg.cookie_secure=True（env SUPERNOVA_WEB_COOKIE_SECURE=1）→ 无条件 secure
    - 否则按请求实际 scheme：HTTPS（含反代 X-Forwarded-Proto）才 secure
    修复：曾一律用 cfg.cookie_secure 且默认 True，而 main() 纯 HTTP 启动 →
    http:// 下浏览器丢弃 cookie → 登录循环。
    """
    if cfg.cookie_secure:
        return True
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).lower()
    return scheme == "https"


def _cookie_kwargs(cfg, request: Request) -> dict:
    return {"httponly": True, "samesite": "lax", "secure": _cookie_secure(cfg, request),
            "max_age": cfg.session_ttl_hours * 3600}


@router.get("/csrf")
def csrf(request: Request):
    cfg = request.app.state.config
    tok = generate_csrf_token()
    resp = JSONResponse({"csrf_token": tok})
    resp.set_cookie("sn-csrf", tok, httponly=False, samesite="lax",
                    secure=_cookie_secure(cfg, request))
    return resp


@router.post("/login")
def login(body: LoginIn, request: Request):
    cfg = request.app.state.config
    if not verify_csrf(request.headers.get("x-csrf-token"), request.cookies.get("sn-csrf")):
        raise HTTPException(status_code=403, detail="invalid csrf token")
    if _brute.is_locked(body.username):
        raise HTTPException(status_code=429, detail="too many attempts, try later")
    store = request.app.state.auth_store
    user = store.get_user_by_username(body.username)
    pw_hash = store.get_password_hash(body.username) if user else None
    ok = pw_hash is not None and verify_password(body.password, pw_hash)
    if not ok:
        _brute.record_failure(body.username)
        raise HTTPException(status_code=401, detail="invalid credentials")
    _brute.reset(body.username)
    sid = request.app.state.session_manager.create(user.id)
    resp = JSONResponse({"user": _user_out(user)})
    resp.set_cookie("sn-sid", sid, **_cookie_kwargs(cfg, request))
    tok = generate_csrf_token()  # 登录后续签 csrf
    resp.set_cookie("sn-csrf", tok, httponly=False, samesite="lax",
                    secure=_cookie_secure(cfg, request),
                    max_age=cfg.session_ttl_hours * 3600)
    return resp


@router.post("/logout")
def logout(request: Request):
    if not verify_csrf(request.headers.get("x-csrf-token"), request.cookies.get("sn-csrf")):
        raise HTTPException(status_code=403, detail="invalid csrf token")
    sid = request.cookies.get("sn-sid")
    if sid:
        request.app.state.session_manager.revoke(sid)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("sn-sid")
    resp.delete_cookie("sn-csrf")
    return resp


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"user": _user_out(user)}
