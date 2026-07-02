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


# Task 4: 模板表（5 类漏洞骨架 + authz 成对 + open_redirect 分流）

_OPEN_REDIRECT_HINTS = ("redirect", "open redirect", "jump", "next", "location header")


def _is_open_redirect(vuln: Any) -> bool:
    technique = (getattr(vuln, "suggested_exploit_technique", None) or "").lower()
    return any(hint in technique for hint in _OPEN_REDIRECT_HINTS)


def _base_spec(vuln: Any, vuln_class: str, endpoints: dict, band: ConfidenceBand) -> HttpRequestSpec:
    method, path = derive_method_path(vuln)
    info = find_endpoint_info(endpoints, path)
    if not method and info and info.get("method"):
        method = info["method"].upper()
    auth_st = derive_auth_state(info)
    return HttpRequestSpec(
        method=method or "GET",
        path=path or "/",
        headers=auth_header(auth_st, info),
        auth_state=auth_st,
        confidence_band=band,
        source_id=getattr(vuln, "ID", ""),
        vuln_class=vuln_class,
    )


def build_template_spec(
    vuln: Any, vuln_class: str, host: str, endpoints: dict, band: ConfidenceBand
) -> HttpRequestSpec | list[HttpRequestSpec] | None:
    """返回 None = 模板无法处理（需 LLM）；list = 成对/多步。"""
    if vuln_class == "authz":
        return _build_authz_pair(vuln, endpoints, band)
    if vuln_class == "auth":
        return None  # 默认走 LLM（§5.3）

    spec = _base_spec(vuln, vuln_class, endpoints, band)
    witness = getattr(vuln, "witness_payload", None) or ""
    if not witness:
        return None  # 无 witness_payload，模板拼不出，交 LLM

    if vuln_class == "injection":
        param = extract_param_name(getattr(vuln, "source", None)) or "id"
        spec.query = {param: witness}
        return spec
    if vuln_class == "xss":
        param = extract_param_name(getattr(vuln, "source", None)) or "q"
        spec.query = {param: witness}
        return spec
    if vuln_class == "ssrf":
        if _is_open_redirect(vuln):
            param = extract_param_name(getattr(vuln, "source", None)) or "next"
            spec.query = {param: witness}
            return spec
        param = getattr(vuln, "vulnerable_parameter", None) or "url"
        spec.method = spec.method if spec.method != "GET" else "POST"
        spec.body = f"{param}={witness}"
        return spec
    return None


def _build_authz_pair(vuln: Any, endpoints: dict, band: ConfidenceBand) -> list[HttpRequestSpec]:
    """§4.4：A 访己（合法）/ A 访 B（越权）成对。"""
    method, path = derive_method_path(vuln)
    info = find_endpoint_info(endpoints, path)
    auth_st = derive_auth_state(info)
    if auth_st != AuthState.REQUIRED:
        auth_st = AuthState.REQUIRED  # authz 漏洞默认需登录
    headers = {"Authorization": "Bearer <AUTH_TOKEN_ATTACKER>"}
    common = dict(
        method=method or "GET", path=path or "/", headers=dict(headers),
        auth_state=auth_st, confidence_band=band,
        source_id=getattr(vuln, "ID", ""), vuln_class="authz",
    )
    legit = HttpRequestSpec(**common, note="合法：访问自己资源（<OWNER_RESOURCE_ID>）")
    cross = HttpRequestSpec(**common, note="越权：访问受害者资源（<VICTIM_RESOURCE_ID>）")
    return [legit, cross]


# Task 5: 富信息 LLM 补缺口
import json as _json

from shannon_core.agents.runner import run_claude_prompt  # 顶层 import 便于 monkeypatch

_LLM_RICH_FIELDS = (
    "ID", "vulnerability_type", "source", "source_endpoint", "endpoint", "path",
    "witness_payload", "sink_call", "vulnerable_code_location",
    "exploitation_hypothesis", "suggested_exploit_technique", "missing_defense",
    "minimal_witness", "evidence_chain", "confidence", "notes",
)

LLM_REQUEST_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"]},
        "path": {"type": ["string", "null"]},
        "query": {"type": ["object", "null"]},
        "headers": {"type": ["object", "null"]},
        "body": {"type": ["string", "null"]},
        "steps": {"type": ["array", "null"]},
    },
    "required": ["method"],
}


def build_llm_prompt(vuln: Any, vuln_class: str, host: str, recon_ctx: dict) -> str:
    fields = {f: getattr(vuln, f) for f in _LLM_RICH_FIELDS if getattr(vuln, f, None)}
    return (
        f"You are reconstructing a replayable HTTP PoC for a confirmed {vuln_class} vulnerability.\n\n"
        f"Target host: {host}\n"
        f"Vulnerability fields:\n{_json.dumps(fields, ensure_ascii=False, indent=2)}\n"
        f"Recon endpoint context:\n{_json.dumps(recon_ctx, ensure_ascii=False, indent=2)}\n\n"
        "Output a JSON object describing the HTTP request shape to reproduce this vulnerability. "
        "Use witness_payload as the attack value. Fill method/path/query/body. "
        "Do NOT include the Authorization/Cookie auth header (added separately). "
        "If multi-step, put each step object in `steps`. Output JSON only."
    )


async def llm_fill_gap(
    vuln: Any, vuln_class: str, host: str, recon_ctx: dict, *,
    repo_path: str, api_key: str | None = None, model_tier: str = "medium",
) -> dict | None:
    """富信息 LLM 补缺口。失败/不可用返回 None（调用方退纯模板+标注）。"""
    prompt = build_llm_prompt(vuln, vuln_class, host, recon_ctx)
    try:
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=repo_path or "/tmp/poc-gen",
            model_tier=model_tier,
            structured_output_schema=LLM_REQUEST_SCHEMA,
            api_key=api_key,
        )
    except Exception:
        return None
    if not getattr(result, "success", False) or not getattr(result, "structured_output", None):
        return None
    return result.structured_output


# Task 6: md 渲染（概览表 + 详细 PoC + 空表兜底）

BAND_LABEL = {
    ConfidenceBand.CONFIRMED: "✓ 已确认",
    ConfidenceBand.HIGH: "● 高置信",
    ConfidenceBand.SUSPECTED: "⚠ 疑似",
}

BAND_FULL = {
    ConfidenceBand.CONFIRMED: "已确认可复现",
    ConfidenceBand.HIGH: "高置信",
    ConfidenceBand.SUSPECTED: "疑似待验证",
}

_AUTH_LABEL = {
    AuthState.NONE: "无需登录",
    AuthState.REQUIRED: "需登录",
    AuthState.UNKNOWN: "未知",
}


def _placeholder_block(has_placeholder: bool) -> str:
    if not has_placeholder:
        return ""
    return (
        "\n> ⚠️ 使用前替换：\n"
        "> - `TARGET[:PORT]` → 实际部署地址\n"
        "> - `<AUTH_TOKEN>` / `<SESSION_COOKIE>` → 有效登录凭证\n"
    )


def _overview_row(vuln_class: str, spec: HttpRequestSpec) -> str:
    auth = _AUTH_LABEL.get(spec.auth_state, "未知")
    if spec.auth_state == AuthState.UNKNOWN:
        auth = "⚠ 未知"
    path_cell = f"{spec.method} {spec.path}"
    return f"| {spec.source_id} | {vuln_class} | {path_cell} | {auth} | {BAND_LABEL[spec.confidence_band]} |"


def _detail_section(vuln_class: str, vuln: Any, spec: HttpRequestSpec, host: str) -> str:
    band_mark = {"confirmed": "✓", "high": "●", "suspected": "⚠"}[spec.confidence_band.value]
    auth = _AUTH_LABEL.get(spec.auth_state, "未知")
    note = f"\n> {spec.note}" if spec.note else ""
    lines = [
        f"### {band_mark} {spec.source_id} · {vuln_class} @ {spec.method} {spec.path}",
        f"**置信度：{BAND_FULL[spec.confidence_band]}** ｜ 认证：{auth} ｜ 来源：{getattr(vuln, 'merge_source', '-')}{note}",
        "",
        "**curl:**",
        "```bash",
        to_curl(spec, host),
        "```",
        "",
        "**Burp Repeater (raw):**",
        "```http",
        to_burp_raw(spec, host),
        "```",
    ]
    return "\n".join(lines)


def render_poc_md(entries, host: str, track: str, *, has_placeholder: bool) -> str:
    """渲染 PoC 集合 Markdown（概览表 + 详细 curl/Burp）。

    Args:
        entries: list[tuple[vuln_class, vuln, HttpRequestSpec | list[HttpRequestSpec]]]
        host: 目标 host（可能为占位符）
        track: "whitebox" 或 "blackbox"
        has_placeholder: 是否显示占位符替换说明块

    Returns:
        完整 PoC 文档 Markdown 字符串
    """
    track_cn = "白盒" if track == "whitebox" else "黑盒"
    counts = {b: 0 for b in ConfidenceBand}
    for _, _, spec_or_list in entries:
        specs = spec_or_list if isinstance(spec_or_list, list) else [spec_or_list]
        for s in specs:
            counts[s.confidence_band] += 1
    n = sum(counts.values())
    header = (
        f"# 可利用漏洞 PoC 集合（{track_cn}）\n"
        f"\n> 目标 host: {host} ｜ 生成自 *_exploitation_queue.json\n"
        f"> 共 {n} 条外部可达 PoC · 已确认 {counts[ConfidenceBand.CONFIRMED]} 条 · "
        f"高置信 {counts[ConfidenceBand.HIGH]} 条 · 疑似 {counts[ConfidenceBand.SUSPECTED]} 条"
        f"{_placeholder_block(has_placeholder)}\n"
    )
    if not entries:
        return header.strip() + "\n"
    overview = ["\n## 概览\n", "| ID | 类型 | 路径 | 认证 | 置信度 |", "|----|------|------|------|--------|"]
    for vuln_class, _, spec_or_list in entries:
        specs = spec_or_list if isinstance(spec_or_list, list) else [spec_or_list]
        overview.append(_overview_row(vuln_class, specs[0]))
    detail = ["\n## 详细 PoC\n"]
    for vuln_class, vuln, spec_or_list in entries:
        specs = spec_or_list if isinstance(spec_or_list, list) else [spec_or_list]
        for i, s in enumerate(specs):
            heading = f"（请求 {i+1}/{len(specs)}）" if len(specs) > 1 else ""
            detail.append(_detail_section(vuln_class, vuln, s, host).replace(
                f" · {vuln_class} @ ", f"{heading} · {vuln_class} @ "))
            detail.append("\n---\n")
    return header + "\n".join(overview) + "\n" + "\n".join(detail)


def empty_poc_md(track: str) -> str:
    """空表兜底：无 externally_exploitable 漏洞时调用。"""
    track_cn = "白盒" if track == "whitebox" else "黑盒"
    return f"# 可利用漏洞 PoC 集合（{track_cn}）\n\n本次扫描无 externally_exploitable 漏洞，未生成 PoC。\n"
