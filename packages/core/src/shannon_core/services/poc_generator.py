# packages/core/src/shannon_core/services/poc_generator.py
"""外部可达漏洞 PoC 自动生成（curl / Burp），报告后处理。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConfidenceBand(str, Enum):
    CONFIRMED = "confirmed"   # ✓ 已确认可复现
    HIGH = "high"             # ● 高置信
    SUSPECTED = "suspected"   # ⚠ 疑似


class AuthState(str, Enum):
    NONE = "none"
    REQUIRED = "required"
    UNKNOWN = "unknown"


@dataclass
class HttpRequestSpec:
    method: str
    path: str
    query: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None
    auth_state: AuthState = AuthState.UNKNOWN
    confidence_band: ConfidenceBand = ConfidenceBand.SUSPECTED
    source_id: str = ""
    vuln_class: str = ""
    note: str | None = None                       # 例如「认证未知」「请求形态未推断」
    steps: list["HttpRequestSpec"] | None = None  # 多步 PoC


_METHOD_PATH_RE = re.compile(r"(GET|POST|PUT|DELETE|PATCH)\s+(/[^\s?]+)", re.IGNORECASE)
_PARAM_RE = re.compile(r"[?&](\w+)=")
_NO_AUTH_TOKENS = {"anon", "guest", "none", "public", ""}


def extract_method_path(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    m = _METHOD_PATH_RE.search(text)
    if not m:
        return None, None
    return m.group(1).upper(), m.group(2)


def extract_param_name(text: str | None) -> str | None:
    if not text:
        return None
    m = _PARAM_RE.search(text)
    return m.group(1) if m else None


def derive_method_path(vuln: Any) -> tuple[str | None, str | None]:
    """§5.2.1：authz 用 endpoint；inj/xss/ssrf source 优先，再 path/source_endpoint。"""
    endpoint = getattr(vuln, "endpoint", None)
    if endpoint:
        m, p = extract_method_path(endpoint)
        if m:
            return m, p
    for fld in ("source", "path", "source_endpoint"):
        m, p = extract_method_path(getattr(vuln, fld, None))
        if m:
            return m, p
    return None, None


def resolve_host(target_url: str | None) -> str:
    if target_url and target_url.strip():
        return target_url.strip().rstrip("/")
    return "https://TARGET[:PORT]"


def classify_confidence(vuln: Any, *, is_accepted: bool) -> ConfidenceBand:
    """§6.3：verdict==vulnerable 或黑盒 accepted → CONFIRMED（优先于 confidence）。"""
    if is_accepted:
        return ConfidenceBand.CONFIRMED
    verdict = (getattr(vuln, "verdict", None) or "").strip().lower()
    if verdict == "vulnerable":
        return ConfidenceBand.CONFIRMED
    confidence = (getattr(vuln, "confidence", None) or "").strip().lower()
    if confidence == "high":
        return ConfidenceBand.HIGH
    return ConfidenceBand.SUSPECTED


def derive_auth_state(endpoint_info: dict | None) -> AuthState:
    if not endpoint_info:
        return AuthState.UNKNOWN
    auth = (endpoint_info.get("auth") or "").strip().lower()
    mw = (endpoint_info.get("middleware") or "").lower()
    if any(k in mw for k in ("login", "auth", "jwt", "token", "session")):
        return AuthState.REQUIRED
    if auth in _NO_AUTH_TOKENS:
        return AuthState.NONE
    if auth and auth not in _NO_AUTH_TOKENS:
        return AuthState.REQUIRED
    return AuthState.UNKNOWN


def auth_header(auth_state: AuthState, endpoint_info: dict | None) -> dict[str, str]:
    """§4.2：jwt/token/bearer → Bearer；session/cookie/login → Cookie。"""
    if auth_state != AuthState.REQUIRED:
        return {}
    mw = (endpoint_info or {}).get("middleware", "").lower()
    has_session = any(k in mw for k in ("session", "cookie", "login"))
    has_token = any(k in mw for k in ("jwt", "token", "bearer"))
    if has_session and not has_token:
        return {"Cookie": "session=<SESSION_COOKIE>"}
    return {"Authorization": "Bearer <AUTH_TOKEN>"}
