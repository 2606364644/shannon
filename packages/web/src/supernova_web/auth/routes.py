from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import sso as sso_mod
from .brute import BruteGuard
from .csrf import generate_csrf_token, verify_csrf
from .dependencies import current_user, require_admin
from .models import User
from .passwords import hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

_brute = BruteGuard()
_log = logging.getLogger("supernova_web.auth")


def clear_login_failures(username: str) -> None:
    """清除某用户的登录失败计数/锁定（供 admin 重置密码后调用）。

    重置语义 = 旧凭证全部作废，锁定的依据（失败计数）无继续存在的理由；
    现场场景：用户反复输错被锁 5 分钟（正确密码也 429），找 admin 重置后
    应立即可用新密码登录，而非再等锁过期。
    """
    _brute.reset(username)


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


# 新密码最小长度（change-password 校验）。弱默认密码（如 123456）改密时应强制
# 不少于 8 位，避免用户把默认弱密码改成另一个弱密码。
_NEW_PASSWORD_MIN_LEN = 8


def _user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role,
            "must_change_password": u.must_change_password,
            "pinned_workspace": u.pinned_workspace,
            "avatar_url": u.avatar_url,
            "auth_provider": u.auth_provider}


def _check_csrf(request: Request) -> None:
    """写端点显式 CSRF 校验（名字/签名对齐 api/users.py 同名惯例；无效 token → 403）。"""
    if not verify_csrf(request.headers.get("x-csrf-token"), request.cookies.get("sn-csrf")):
        raise HTTPException(status_code=403, detail="invalid csrf token")


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


def _cookie_kwargs(cfg, request: Request, ttl_hours: int | None = None) -> dict:
    ttl = ttl_hours if ttl_hours is not None else cfg.session_ttl_hours
    return {"httponly": True, "samesite": "lax", "secure": _cookie_secure(cfg, request),
            "max_age": ttl * 3600}


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


@router.post("/change-password")
def change_password(body: ChangePasswordIn, request: Request, user: User = Depends(current_user)):
    """已登录用户改密码。需 CSRF + 登录态；校验旧密码正确、新密码合法后改 hash
    并清 must_change_password。默认账号（must_change_password=True）改密后即脱提醒。"""
    if not verify_csrf(request.headers.get("x-csrf-token"), request.cookies.get("sn-csrf")):
        raise HTTPException(status_code=403, detail="invalid csrf token")
    if len(body.new_password) < _NEW_PASSWORD_MIN_LEN:
        raise HTTPException(status_code=400, detail=f"new password must be at least {_NEW_PASSWORD_MIN_LEN} characters")
    if body.new_password == body.old_password:
        raise HTTPException(status_code=400, detail="new password must differ from old")
    store = request.app.state.auth_store
    pw_hash = store.get_password_hash(user.username)
    if pw_hash is None or not verify_password(body.old_password, pw_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    store.update_password(user.id, hash_password(body.new_password))
    return {"ok": True}


# ── SSO（富途 OA passport，spec 2026-08-25 §5.2）──────────────────────────────

@router.get("/sso/config")
def sso_config(request: Request):
    """公开：前端登录页据此渲染/隐藏「使用 OA 账号登录」按钮。"""
    return {"enabled": request.app.state.config.sso_enabled}


@router.get("/sso/login")
def sso_login(request: Request, next: str = "/"):
    cfg = request.app.state.config
    if not cfg.sso_enabled:
        raise HTTPException(status_code=404, detail="sso disabled")
    url = sso_mod.build_passport_login_url(cfg.sso_passport_base, cfg.sso_public_base_url, next)
    return RedirectResponse(url, status_code=302)


@router.get("/sso/callback")
def sso_callback(request: Request, AUTH_TICKET: str = "", next: str = "/"):
    """OA 登录后 302 回调。校验链（spec §5.2）：开关→ticket 存在→防重放→
    validateTicket→白名单→JIT 建户→建 session。任一失败 302 /login?sso_error=<code>。"""
    cfg = request.app.state.config
    store = request.app.state.auth_store
    if not cfg.sso_enabled:
        raise HTTPException(status_code=404, detail="sso disabled")
    next_path = sso_mod.safe_next(next)

    def _fail(code: str) -> RedirectResponse:
        return RedirectResponse(f"/login?sso_error={code}", status_code=302)

    if not AUTH_TICKET:
        return _fail("missing_ticket")
    if store.is_ticket_used(AUTH_TICKET):
        return _fail("replayed_ticket")
    try:
        info = sso_mod.validate_ticket(cfg.sso_passport_base, cfg.sso_auth_domain, AUTH_TICKET)
    except sso_mod.SsoTicketError as exc:
        # 安全收口：只落机器码 + ticket 前 8 位掩码——upstream_error 的 message 含完整
        # validateTicket URL（即 ticket 明文），禁 str(exc)/__cause__/traceback 入日志。
        _log.warning("sso ticket rejected: %s (ticket=%s…)", exc.code, AUTH_TICKET[:8])
        return _fail(exc.code)
    if not store.is_nick_whitelisted(info.nick):
        _log.warning("sso nick not whitelisted: %r", info.nick)
        return _fail("not_whitelisted")
    store.mark_ticket_used(AUTH_TICKET)

    # JIT 建户：随机不可逆密码 hash——SSO 户无法走账密登录（spec §5.2）。
    # 撞名护栏（最终审查 Important-1）：OA nick 命中本地账密户（auth_provider != "sso"）
    # 时拒绝——静默合并等于把本地账户拱手让给 OA 同 nick 持有者（接管面）；
    # 不建会话、不 update_avatar，仅既有 SSO 户走复用 + avatar 刷新。
    user = store.get_user_by_username(info.nick)
    if user is not None and user.auth_provider != "sso":
        _log.warning("sso nick conflicts with local password account: %r", info.nick)
        return _fail("nick_conflict")
    if user is None:
        user = store.create_user(info.nick, hash_password(secrets.token_urlsafe(32)),
                                 role="user", auth_provider="sso")
    store.update_avatar(user.id, info.avatar_url)

    sid = request.app.state.session_manager.create(
        user.id, ttl_hours=cfg.sso_session_ttl_hours, auth_method="sso")
    resp = RedirectResponse(next_path, status_code=302)
    resp.set_cookie("sn-sid", sid, **_cookie_kwargs(cfg, request, ttl_hours=cfg.sso_session_ttl_hours))
    tok = generate_csrf_token()  # 对齐账密登录：建会话后续签 csrf
    resp.set_cookie("sn-csrf", tok, httponly=False, samesite="lax",
                    secure=_cookie_secure(cfg, request),
                    max_age=cfg.sso_session_ttl_hours * 3600)
    return resp


class WhitelistIn(BaseModel):
    nick: str


@router.get("/sso/whitelist")
def sso_whitelist_list(request: Request):
    # 关闭态 404 必须先于 admin 判定：Depends(require_admin) 在函数体之前执行，
    # 未登录请求会先撞 401（spec「SSO 关闭 404」且不向未认证方泄露端点存在），
    # 故此处函数体内先查开关再手动调 require_admin（其读 request.state.user，直接调用等价）。
    if not request.app.state.config.sso_enabled:
        raise HTTPException(status_code=404, detail="sso disabled")
    require_admin(request)
    rows = request.app.state.auth_store.get_sso_whitelist()
    return {"whitelist": [{"nick": r[0], "added_by": r[1], "created_at": r[2]} for r in rows]}


@router.post("/sso/whitelist")
def sso_whitelist_add(body: WhitelistIn, request: Request, admin: User = Depends(require_admin)):
    # 写端点显式 CSRF（最终审查采纳项）：对齐 users.py/_check_csrf 惯例；
    # 前端 client.ts 对 POST/DELETE 自动注入 X-CSRF-Token，零适配成本。
    _check_csrf(request)
    nick = body.nick.strip()
    if not nick:
        raise HTTPException(status_code=422, detail="nick must not be blank")
    request.app.state.auth_store.add_sso_whitelist(nick, admin.username)
    return {"ok": True}


@router.delete("/sso/whitelist/{nick}")
def sso_whitelist_remove(nick: str, request: Request, _admin: User = Depends(require_admin)):
    _check_csrf(request)  # 同上：写端点显式 CSRF
    request.app.state.auth_store.remove_sso_whitelist(nick)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request):
    if not verify_csrf(request.headers.get("x-csrf-token"), request.cookies.get("sn-csrf")):
        raise HTTPException(status_code=403, detail="invalid csrf token")
    cfg = request.app.state.config
    sid = request.cookies.get("sn-sid")
    # SSO 会话登出：响应带 OA 登出跳转 URL（前端清态后 assign；账密会话为 None 维持原行为）。
    # 顺序铁律：先 get_session 拿 auth_method 再 revoke——revoke 后 get_session 返 None。
    sso_logout_url = None
    if cfg.sso_enabled and sid:
        row = request.app.state.auth_store.get_session(sid)
        if row is not None and row.auth_method == "sso":
            sso_logout_url = sso_mod.build_passport_logout_url(
                cfg.sso_passport_base, cfg.sso_public_base_url)
    if sid:
        request.app.state.session_manager.revoke(sid)
    resp = JSONResponse({"ok": True, "sso_logout_url": sso_logout_url})
    resp.delete_cookie("sn-sid")
    resp.delete_cookie("sn-csrf")
    return resp


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"user": _user_out(user)}
