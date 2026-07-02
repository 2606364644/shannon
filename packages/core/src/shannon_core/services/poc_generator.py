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


# Task 2: recon 端点表解析
from pathlib import Path

_SECURITY_CTX_RE = re.compile(r"^#{2,3}\s+(?:[\d.]+\s+)?Endpoint Security Context.*$", re.MULTILINE)


def _find_security_context_section(md: str) -> str | None:
    m = _SECURITY_CTX_RE.search(md)
    if not m:
        return None
    rest = md[m.start():]
    nxt = re.search(r"\n#{2,3}\s[\d.]*[A-Za-z]", rest[1:])
    section = rest[: nxt.start() + 1] if nxt else rest
    idx = section.find("|")
    return section[idx:] if idx >= 0 else None


def parse_recon_endpoints(recon_path: Path) -> dict[str, dict]:
    """解析 recon_deliverable.md 的 Endpoint Security Context 表。

    section 编号跨轨不稳（黑盒 ## 4.2 / 白盒 ## 2.1），用关键词定位。
    列名 Path / Endpoint Path 宽容匹配。返回 {path_lower: {method,auth,middleware}}。
    """
    if not recon_path.exists():
        return {}
    from shannon_core.code_index.gitnexus_mcp import _parse_md_table

    md = recon_path.read_text(encoding="utf-8")
    section = _find_security_context_section(md)
    if not section:
        return {}
    out: dict[str, dict] = {}
    for row in _parse_md_table(section):
        path = (row.get("Path") or row.get("Endpoint Path") or "").strip().strip("`")
        if not path:
            continue
        out[path.lower()] = {
            "method": (row.get("Method") or "").strip(),
            "auth": (row.get("Auth") or row.get("Required Role") or "").strip(),
            "middleware": (row.get("Middleware") or "").strip(),
        }
    return out


def find_endpoint_info(endpoints: dict[str, dict], path: str | None) -> dict | None:
    if not path or not endpoints:
        return None
    key = path.lower()
    if key in endpoints:
        return endpoints[key]
    # 前缀匹配：path 可能带具体 id，recon 里是 :param 模板
    for ep_key, info in endpoints.items():
        # 检查是否为相同端点（参数部分不同）
        base_key = ep_key.split("/:")[0] if "/:" in ep_key else ep_key.rstrip("/").rstrip("/*")
        base_path = key.split("/:")[0] if "/:" in key else key
        if base_key and base_path.startswith(base_key):
            return info
    return None


# Task 3: curl / Burp raw 双格式化
from urllib.parse import urlencode


def _host_only(host: str) -> str:
    return (
        host.replace("https://", "").replace("http://", "").rstrip("/")
    )


def to_curl(spec: HttpRequestSpec, host: str) -> str:
    url = host.rstrip("/") + spec.path
    if spec.query:
        url += "?" + urlencode(spec.query)
    parts = [f"curl -i -X {spec.method} '{url}'"]
    for k, v in spec.headers.items():
        parts.append(f"  -H '{k}: {v}'")
    if spec.body:
        parts.append(f"  --data '{spec.body}'")
    return " \\\n".join(parts)


def to_burp_raw(spec: HttpRequestSpec, host: str) -> str:
    path = spec.path
    if spec.query:
        path += "?" + "&".join(f"{k}={v}" for k, v in spec.query.items())  # 原始 payload
    lines = [f"{spec.method} {path} HTTP/1.1", f"Host: {_host_only(host)}"]
    for k, v in spec.headers.items():
        lines.append(f"{k}: {v}")
    if spec.body:
        lines.append("Content-Type: application/x-www-form-urlencoded")
        lines.append(f"Content-Length: {len(spec.body.encode('utf-8'))}")
    lines.append("")
    if spec.body:
        lines.append(spec.body)
    lines.append("")
    return "\n".join(lines)
