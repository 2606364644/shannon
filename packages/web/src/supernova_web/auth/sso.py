"""富途 OA passport SSO 域逻辑（spec 2026-08-25 §5）。

纯逻辑（URL 拼接/next 防护/响应校验）+ 可注入 transport 的 validateTicket
客户端；安全判定集中此处，路由层（routes.py）只做编排。
"""
from __future__ import annotations

import time
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from .models import SsoConfig

CALLBACK_PATH = "/api/auth/sso/callback"


def resolve_runtime(cfg: SsoConfig) -> SsoConfig:
    """DB 原始配置 → 运行时配置（spec 2026-08-26 §7.2）：
    public_base_url 空 → 回落 https://{auth_domain}（对齐原 WebConfig 语义）；
    尾部斜杠归一。纯函数不改动入参（存库原始值保持可回显）。"""
    public = cfg.public_base_url or (f"https://{cfg.auth_domain}" if cfg.auth_domain else "")
    return SsoConfig(enabled=cfg.enabled, auth_domain=cfg.auth_domain,
                     public_base_url=public.rstrip("/"), passport_base=cfg.passport_base,
                     session_ttl_hours=cfg.session_ttl_hours,
                     updated_at=cfg.updated_at, updated_by=cfg.updated_by)


class SsoTicketError(Exception):
    """ticket 校验失败。code 是面向 /login?sso_error= 的机器码（前端 i18n 映射）。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class SsoUserInfo(BaseModel):
    nick: str
    avatar_url: str | None = None
    uid: int | None = None


def safe_next(raw: str | None) -> str:
    """next 仅允许站内相对路径（spec §5.3 防 open redirect）：
    非空、/ 开头、不以 // 或 /\\ 开头；不合法回落 /。"""
    if not raw or not raw.startswith("/") or raw.startswith("//") or raw.startswith("/\\"):
        return "/"
    return raw


def build_passport_login_url(passport_base: str, public_base: str, next_path: str) -> str:
    """OA 登录页 302 目标：returnUrl = 编码后的回调地址（内嵌再编码的 next）。"""
    callback = f"{public_base}{CALLBACK_PATH}?{urlencode({'next': safe_next(next_path)})}"
    return f"{passport_base}/site/login.html?{urlencode({'returnUrl': callback})}"


def build_passport_logout_url(passport_base: str, public_base: str) -> str:
    """OA 登出页 302 目标：returnUrl = 编码后的本站登录页（登出后回到两方式登录页）。"""
    return f"{passport_base}/site/logout.html?{urlencode({'returnUrl': f'{public_base}/login'})}"


def parse_validate_response(payload: dict, now: float) -> SsoUserInfo:
    """validateTicket 响应校验（spec §5.2 链 4-5）：result/code、nick 非空、oaToken 时间窗。"""
    if payload.get("result") != 0 or payload.get("code") != 0:
        raise SsoTicketError("invalid_response",
                             f"passport rejected: result={payload.get('result')} code={payload.get('code')}")
    data = payload.get("data") or {}
    info = data.get("userInfo") or {}
    nick = (info.get("nick") or "").strip()
    if not nick:
        raise SsoTicketError("missing_nick", "userInfo.nick empty")
    init_t, invalid_t = data.get("oaTokenInitTime"), data.get("oaTokenInvalidTime")
    if not isinstance(init_t, (int, float)) or not isinstance(invalid_t, (int, float)):
        raise SsoTicketError("invalid_response", "oaToken time fields missing/non-numeric")
    if now < init_t:
        raise SsoTicketError("token_not_yet_valid", "oaToken not yet valid")
    if now >= invalid_t:
        raise SsoTicketError("token_expired", "oaToken expired")
    return SsoUserInfo(nick=nick, avatar_url=info.get("avatarUrl"), uid=info.get("uid"))


def validate_ticket(passport_base: str, auth_domain: str, ticket: str, *,
                    transport: httpx.BaseTransport | None = None,
                    now: float | None = None) -> SsoUserInfo:
    """组合：服务端 GET validateTicket（§协议 2）+ 解析校验。
    网络/HTTP/JSON 异常归一为 SsoTicketError('upstream_error')，路由层只 catch 一种。"""
    try:
        with httpx.Client(timeout=10.0, transport=transport) as client:
            resp = client.get(f"{passport_base}/api/v1/validateTicket",
                              params={"authTicket": ticket, "authDomain": auth_domain})
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        raise SsoTicketError("upstream_error", f"validateTicket call failed: {exc}") from exc
    return parse_validate_response(payload, time.time() if now is None else now)
