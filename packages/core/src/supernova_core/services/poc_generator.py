# packages/core/src/supernova_core/services/poc_generator.py
"""外部可达漏洞 PoC 自动生成（curl / Burp），报告后处理。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from supernova_core.agents.runner import run_claude_prompt  # 模块级名称，便于测试 monkeypatch
from supernova_core.audit.session_registry import get_audit_session
from supernova_core.display.formatters import format_duration
from supernova_core.models.queue_schemas import VulnerabilityQueue


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
    """§6.3 + G2（spec §3.5，治 P0-2 置信虚标）：

    - 黑盒 accepted（重放证据）→ CONFIRMED（不变）。
    - 白盒 verdict==vulnerable **且** confidence ∉ {needs_review, low} → CONFIRMED
      （缺省 confidence 视为可确认，向后兼容）；needs_review/low → SUSPECTED。
    """
    if is_accepted:
        return ConfidenceBand.CONFIRMED
    verdict = (getattr(vuln, "verdict", None) or "").strip().lower()
    confidence = (getattr(vuln, "confidence", None) or "").strip().lower()
    if verdict == "vulnerable" and confidence not in ("needs_review", "low"):
        return ConfidenceBand.CONFIRMED
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
    from supernova_core.code_index.gitnexus_mcp import _parse_md_table

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


def _host_only(host: str) -> str:
    return (
        host.replace("https://", "").replace("http://", "").rstrip("/")
    )


def _sh_quote(value: str) -> str:
    """POSIX 单引号安全包裹：' → '\''（G6，治 P1-7 shell 截断）。"""
    return "'" + value.replace("'", "'\\''") + "'"


def _minimal_enc(value: str) -> str:
    """请求行最小编码（G6）：空格/CR/LF/非 ASCII → percent-encode，其余符号保 raw。"""
    out: list[str] = []
    for ch in value:
        if ch == " ":
            out.append("%20")
        elif ch == "\r":
            out.append("%0D")
        elif ch == "\n":
            out.append("%0A")
        elif ord(ch) > 0x7E:
            out.append("".join(f"%{b:02X}" for b in ch.encode("utf-8")))
        else:
            out.append(ch)
    return "".join(out)


def to_curl(spec: HttpRequestSpec, host: str) -> str:
    url = host.rstrip("/") + spec.path
    if spec.query:
        url += "?" + urlencode(spec.query)
    parts = [f"curl -i -X {spec.method} {_sh_quote(url)}"]
    for k, v in spec.headers.items():
        parts.append(f"  -H {_sh_quote(f'{k}: {v}')}")
    if spec.body:
        parts.append(f"  --data {_sh_quote(spec.body)}")
    return " \\\n".join(parts)


def to_burp_raw(spec: HttpRequestSpec, host: str) -> str:
    path = spec.path
    if spec.query:
        path += "?" + "&".join(
            f"{k}={_minimal_enc(v)}" for k, v in spec.query.items())  # 原始 payload，最小编码
    lines = [f"{spec.method} {path} HTTP/1.1", f"Host: {_host_only(host)}"]
    for k, v in spec.headers.items():
        if k.lower() in ("host", "content-type"):
            continue  # Host/Content-Type 由渲染层唯一产出（G6 header 去重）
        lines.append(f"{k}: {v}")
    if spec.body:
        # LLM gap-fill 可能返回 JSON body（如 {"id_token":"forged"}），按 body 形态判定 Content-Type，
        # 避免对 JSON body 强加 form-urlencoded 致服务器拒绝。
        body_stripped = spec.body.lstrip()
        if body_stripped[:1] in ("{", "["):
            lines.append("Content-Type: application/json")
        else:
            lines.append("Content-Type: application/x-www-form-urlencoded")
        lines.append(f"Content-Length: {len(spec.body.encode('utf-8'))}")
    lines.append("")
    if spec.body:
        lines.append(spec.body)
    lines.append("")
    return "\n".join(lines)


# G3/G5（spec §3.3）：输出 lint —— 写盘前统一关卡，治 P0-3 路由/方法污染与 P1-6 占位符泄漏。

_PATH_OK_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~:/@%!$&+<>")
_METHOD_WHITELIST = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
_PLACEHOLDER_RES = (
    re.compile(r"witness_payload", re.IGNORECASE),
    re.compile(r"\$\{[^}]*\}"),
    re.compile(r"\{\{[^}]*\}\}"),
)


def _lint_path(path: str | None) -> str:
    """path 白名单截断：从首个非法字符截（残片 `,` `;` 全角 `）` 通配 `*`），
    不以 / 开头补 /，空落 /。"""
    if not path:
        return "/"
    kept = []
    for ch in path:
        if ch not in _PATH_OK_CHARS:
            break
        kept.append(ch)
    out = "".join(kept)
    if not out.startswith("/"):
        out = "/" + out
    return out or "/"


def _has_placeholder(value: str) -> bool:
    return any(r.search(value) for r in _PLACEHOLDER_RES)


def lint_spec(spec: HttpRequestSpec) -> HttpRequestSpec:
    """原地 lint 单个 spec（幂等）：path/method 白名单、占位符黑名单、header 归一。"""
    notes: list[str] = []
    if spec.path is None or spec.path != (cleaned := _lint_path(spec.path)):
        if spec.path and cleaned != spec.path:
            notes.append(f"path 截断：{spec.path!r} → {cleaned!r}")
        spec.path = cleaned
    if spec.method not in _METHOD_WHITELIST:
        original = spec.method
        spec.method = "POST" if spec.body else "GET"
        notes.append(f"method {original!r} 非白名单，按参数位推导为 {spec.method}")
    for k in list(spec.headers):
        if k.lower() in ("content-type", "host"):
            del spec.headers[k]
        elif _has_placeholder(spec.headers[k]):
            notes.append(f"header {k} 含 LLM 模板占位符，已剔除，需手工补全")
            del spec.headers[k]
    for k in list(spec.query):
        if _has_placeholder(spec.query[k]):
            notes.append(f"query 参数 {k} 含 LLM 模板占位符，已剔除，需手工补全")
            del spec.query[k]
    if spec.body and _has_placeholder(spec.body):
        notes.append("body 含 LLM 模板占位符，需手工补全")
        spec.body = None
    if notes:
        spec.note = "；".join(filter(None, [spec.note, *notes]))
    return spec


# Task 4: 模板表（5 类漏洞骨架 + authz 成对 + open_redirect 分流）

_OPEN_REDIRECT_HINTS = ("redirect", "open redirect", "jump", "next", "location header")


def _is_open_redirect(vuln: Any) -> bool:
    technique = (getattr(vuln, "suggested_exploit_technique", None) or "").lower()
    return any(hint in technique for hint in _OPEN_REDIRECT_HINTS)


# 请求体确定性信号（source/evidence_chain 里的框架形态，小写匹配）：
# NodeGoat/Express 'req.body.preTax'、Koa 'ctx.request.body'、Spring '@RequestBody'、
# LLM 轨中文 source 'POST body 字段: firstName（…表单）'（NodeGoat xss 实证）。
_BODY_SIGNALS = ("req.body", "request.body", "@requestbody", "req.body[", "request.body[",
                 "body 字段", "body字段")

_BODY_PARAM_RE = re.compile(r"(?:req|request)\.body(?:\.|\['?)(\w+)")


def _extract_body_param(source: str | None) -> str | None:
    """从 source 提取请求体参数名（'req.body.preTax @ f.js:71' → 'preTax'）。"""
    if not source:
        return None
    m = _BODY_PARAM_RE.search(source)
    return m.group(1) if m else None


# G1（spec §3.1）：witness 契约与确定性解析 —— 治 P0-1「witness 自由文本整体塞 fallback 参数」。
# 三形态按优先级：请求行 → 参数串 → 纯值；尾部中文注解先剥（进 note）。

_CJK_CHAR_RE = re.compile(r"[一-鿿]")
_ANNOTATION_TAIL_RE = re.compile(r"[（(]([^()（）]*)[)）][ \t]*$")
_REQ_LINE_RE = re.compile(r"^(GET|POST|PUT|DELETE|PATCH)\s+(/[^\s?#]*)(\?(\S.*))?", re.IGNORECASE)
_PARAM_SEG_RE = re.compile(r"^(\w+)=(.*)$", re.DOTALL)
_QUERY_CJK_TAIL_RE = re.compile(r"\s(?=[一-鿿])")


@dataclass
class WitnessParse:
    """parse_witness 产物：values 直接落位（placement 决定 query/body），
    raw 为单参数值（现状语义），note 合并进 spec.note。"""
    values: dict[str, str] = field(default_factory=dict)
    method: str | None = None
    path: str | None = None
    raw: str | None = None
    note: str | None = None

    @property
    def has_payload(self) -> bool:
        return bool(self.values) or bool((self.raw or "").strip())


def _strip_trailing_annotation(text: str) -> tuple[str, str | None]:
    """剥尾部注解：末尾 （…）/(…) 且内容含 CJK（全角括号本身即 CJK 语境标志）。

    ASCII-only payload 尾部括号（alert(1)）不剥（R4），须内容含 CJK 才剥。
    """
    m = _ANNOTATION_TAIL_RE.search(text.rstrip())
    if m and _CJK_CHAR_RE.search(m.group(1)):
        stripped = text.rstrip()[: m.start()].rstrip()
        return stripped, m.group(0).strip()
    return text, None


def _split_param_segments(qs: str) -> dict[str, str]:
    """'a=1&b=2' 全量展开；非 key=value 形态的段（如 '...'）丢弃。"""
    out: dict[str, str] = {}
    for seg in qs.split("&"):
        m = _PARAM_SEG_RE.match(seg)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _strip_query_cjk_tail(qs: str) -> tuple[str, str | None]:
    """query 串尾部「空格+CJK 开头」说明段剥离（'uid=1' OR 1=1 触发报错'）。

    只剥最尾部一段（从最后一个「空格且其后到串尾含 CJK」处切）；纯 ASCII 保持原样。
    """
    m = _QUERY_CJK_TAIL_RE.search(qs)
    if not m:
        return qs, None
    head, tail = qs[: m.start()], qs[m.start():].strip()
    if not head or not _CJK_CHAR_RE.search(tail):
        return qs, None
    return head, tail


def parse_witness(witness: str | None) -> WitnessParse:
    """确定性解析 witness_payload 三形态（spec §3.1）。

    1. 请求行 `METHOD /path?k=v`：method/path/query 全量展开，剩余说明 → note。
    2. 参数串 `a=b&c=d`（每段 key= 前缀）：展开 values。
    3. 纯值：raw（单参数值，现状语义）。
    尾部注解（含 CJK 的括号段）先剥。
    """
    if not witness or not witness.strip():
        return WitnessParse()
    core, note = _strip_trailing_annotation(witness.strip())
    m = _REQ_LINE_RE.match(core)
    if m:
        method = m.group(1).upper()
        path = m.group(2) or "/"
        rest = core[m.end():].strip()
        if rest:
            note = f"{note}；{rest}" if note else rest
        qs = m.group(4)
        if qs:
            qs, q_note = _strip_query_cjk_tail(qs)
            if q_note:
                note = f"{note}；{q_note}" if note else q_note
            values = _split_param_segments(qs)
        else:
            values = {}
        return WitnessParse(values=values, method=method, path=path, note=note or None)
    segs = core.split("&")
    if all(_PARAM_SEG_RE.match(s) for s in segs):
        values = _split_param_segments(core)
        if values:
            return WitnessParse(values=values, note=note or None)
    return WitnessParse(raw=core, note=note or None)


# G3（spec §3.2）：RouteIndex —— entry_points.json join 补 method/route（报告层消费）。

_FUNC_BLOCK_ID_RE = re.compile(r"^(.*?):([^/:]*):(\d+)$")


def _file_basename(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


class RouteIndex:
    """entry_points.json 的 adjudicated_entry_points 路由索引。

    三级 resolve（spec §3.2 + NodeGoat 实证补充第 3 级）：
    A. (basename, handler) 精确 join（Spring/Koa 具名 handler）
    B. basename + 行号邻近（同文件多 handler）
    C. basename stem ↔ route 整段匹配（Express 匿名回调：路由全挂在 router 文件
       的 index handler 下，vuln 的 handler 文件名与路由段同名，如 contributions.js
       ↔ /contributions；NodeGoat 23 条 entry 里 20 条挂 index.js:index:11，A/B 必 miss）
    候选多路由时按 placement 偏好 method（body→POST，query→GET）。
    """

    def __init__(self, entry_points: list[dict] | None):
        self._by_handler: dict[tuple[str, str], list[tuple[str | None, str]]] = {}
        self._by_file: dict[str, list[tuple[int, str | None, str]]] = {}
        self._all: list[tuple[str | None, str]] = []
        for ep in entry_points or []:
            fb = (ep.get("func_block_id") or "").strip()
            route = (ep.get("route") or "").strip()
            method = (ep.get("http_method") or "").strip().upper() or None
            if not route or not route.startswith("/"):
                continue  # 无路由信息的条目（如 xxx.js:Handler:6 | None None）
            m = _FUNC_BLOCK_ID_RE.match(fb)
            if m:
                f, handler, line = _file_basename(m.group(1)), m.group(2), int(m.group(3))
            else:
                f, handler, line = _file_basename(fb), "", 0
            if method in ("MIDDLEWARE",) or method and method not in (
                    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                continue  # MIDDLEWARE /tutorial 等非路由方法不进索引
            entry = (method, route)
            self._all.append(entry)
            if handler:
                self._by_handler.setdefault((f, handler), []).append(entry)
            self._by_file.setdefault(f, []).append((line, method, route))

    @staticmethod
    def _pick(candidates: list[tuple[str | None, str]],
              placement: str | None) -> tuple[str | None, str] | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        prefer = "POST" if placement == "body" else "GET"
        for method, route in candidates:
            if method == prefer:
                return (method, route)
        return candidates[0]

    def _stem_candidates(self, stem: str) -> list[tuple[str | None, str]]:
        seg = f"/{stem}"
        out = []
        for method, route in self._all:
            parts = route.split("?")[0].strip("/").split("/")
            if parts and parts[0] == stem and route.startswith(seg):
                out.append((method, route))
        return out

    def resolve(
        self, *, file: str | None = None, handler: str | None = None,
        line: int | None = None, placement: str | None = None,
    ) -> tuple[str | None, str | None]:
        """返回 (http_method, route)；全 miss → (None, None)。"""
        if file:
            base = _file_basename(file)
            if handler:
                hit = self._pick(self._by_handler.get((base, handler), []), placement)
                if hit and hit[0]:
                    return hit
            if line is not None and base in self._by_file:
                near = min(self._by_file[base], key=lambda t: abs(t[0] - line))
                if near[1]:
                    return (near[1], near[2])
            stem = base.rsplit(".", 1)[0] if "." in base else base
            hit = self._pick(self._stem_candidates(stem), placement)
            if hit and hit[0]:
                return hit
        return (None, None)


def _infer_placement(vuln: Any, vuln_class: str) -> str:
    """确定性参数位推断："query" | "body"。

    修「injection/xss 恒 query」：POST JSON/form API（req.body.x / @RequestBody）
    的 witness 放 query 会拼出错误请求形态。信号缺失时维持类兜底（inj/xss →
    query，ssrf 非 redirect → body），与历史行为一致；gap-fill 路径上 LLM 的
    param_location 优先于此推断（见 _assemble）。
    """
    if vuln_class == "ssrf":
        return "query" if _is_open_redirect(vuln) else "body"
    text = " ".join([
        getattr(vuln, "source", None) or "",
        getattr(vuln, "evidence_chain", None) or "",
    ]).lower()
    if any(sig in text for sig in _BODY_SIGNALS):
        return "body"
    return "query"


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
    """返回 None = 模板无法处理（需分层路径/LLM）；list = 成对/多步。

    G3（spec §3.2，修 07-22 实现偏差①）：inj/xss/ssrf 分支退役——一律走
    `_extract_deterministic → 完整? _assemble : gapped` 分层路径，缺路由不再拿
    witness 硬拼模板（治 GET / 塌缩）。本函数仅保留 authz（成对模板）/auth（LLM）。
    """
    if vuln_class == "authz":
        return _build_authz_pair(vuln, endpoints, band)
    return None  # auth 与 inj/xss/ssrf：分层路径（auth per-item LLM，见 _build_entry）


_AUTHZ_PATH_PARAM_RE = re.compile(r"/:(\w+)")
_AUTHZ_REQ_PARAM_RE = re.compile(r"req\.params\.(\w+)")


def _find_authz_resource_param(vuln: Any, path: str | None) -> tuple[str, str] | None:
    """资源参数发现（spec §3.4）：返回 (where, name)，where ∈ {"path","body"}。

    path 模板段 `:userId` 优先；次选 source 里 req.params.X（且 X 在 path 模板段）；
    再选 req.body.X（body 字段）。都无 → None（无资源对象）。
    """
    seg = _AUTHZ_PATH_PARAM_RE.search(path or "")
    if seg:
        return ("path", seg.group(1))
    source = getattr(vuln, "source", None) or ""
    m = _AUTHZ_REQ_PARAM_RE.search(source)
    if m and path and f":{m.group(1)}" in path:
        return ("path", m.group(1))
    m = _BODY_PARAM_RE.search(source)
    if m:
        return ("body", m.group(1))
    return None


def _build_authz_pair(
    vuln: Any, endpoints: dict, band: ConfidenceBand
) -> HttpRequestSpec | list[HttpRequestSpec]:
    """§4.4：A 访己（合法）/ A 访 B（越权）成对；G4（spec §3.4）配对鉴别力升级。

    资源参数命中 path 段 → legit/cross 分别替换 <OWNER_RESOURCE_ID>/<VICTIM_RESOURCE_ID>
    （两请求在请求位真实不同）；命中 body 字段 → body 值替换。
    无资源参数（Vertical/BFLA，如 GET /benefits）→ 诚实降单请求 + 无鉴别力标注
    （治 P0-4「逐字节相同的成对请求」）。
    """
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
    res = _find_authz_resource_param(vuln, path)
    if not res:
        spec = HttpRequestSpec(
            **common, note="无资源对象：合法/越权请求无差异（角色差异在凭证），配对无鉴别力")
        return spec
    where, name = res
    legit = HttpRequestSpec(**common, note=f"合法：访问自己资源（{name}=<OWNER_RESOURCE_ID>）")
    cross = HttpRequestSpec(**common, note=f"越权：访问受害者资源（{name}=<VICTIM_RESOURCE_ID>）")
    if where == "path":
        legit.path = (common["path"]).replace(f":{name}", "<OWNER_RESOURCE_ID>")
        cross.path = (common["path"]).replace(f":{name}", "<VICTIM_RESOURCE_ID>")
    else:
        if legit.method == "GET":
            legit.method = cross.method = "POST"
        legit.body = f"{name}=<OWNER_RESOURCE_ID>"
        cross.body = f"{name}=<VICTIM_RESOURCE_ID>"
    return [legit, cross]


# Task 5: 富信息 LLM 补缺口

_GN_SOURCE_RE = re.compile(
    r"^(\S+)\s*\((.+?):([^/:]+):(\d+)\)\s*$"
)
_AT_FILE_LINE_RE = re.compile(r"@?\s*(\S+\.[A-Za-z]\w*):(\d+)\s*$")
_PATH_AT_FILE_LINE_RE = re.compile(r"([\w./\\-]+\.[A-Za-z]\w*):(\d+)")


def extract_gn_location(source: str | None) -> tuple[str | None, str | None, str | None, int | None]:
    """从 GitNexus 轨 source 提取 (param_name, file_path, method, line)。

    GitNexus builder 的 _source_text 产 'param (file:method:line)' 形态
    （如 'payload (…/Controller.java:apiModifyClusterConfig:70)'）。
    file 可含 '/'/'.'；method 是单个标识符（不含 ':/'）；line 是纯数字。
    非 GitNexus 格式（LLM 轨的 '@RequestBody at Foo.java:71' 等）→ (None, None, None, None)。
    """
    if not source:
        return (None, None, None, None)
    m = _GN_SOURCE_RE.match(source.strip())
    if not m:
        return (None, None, None, None)
    return (m.group(1), m.group(2), m.group(3), int(m.group(4)))


def _extract_source_location(source: str | None, path: str | None = None) -> tuple[str | None, int | None]:
    """从 LLM 轨 source/path 提取 (file, line)（RouteIndex join 输入）。

    source 尾部 '@ app/routes/contributions.js:32'；source 无则 path 流程里
    首个裸 'file.ext:line' 引用（xss 实证：'req.body.firstName → session.js:194 …'）。
    非 file.ext:line 形态 → (None, None)。
    """
    if source:
        m = _AT_FILE_LINE_RE.search(source.strip())
        if m:
            return m.group(1), int(m.group(2))
    if path:
        m = _PATH_AT_FILE_LINE_RE.search(path)
        if m:
            return m.group(1), int(m.group(2))
    return (None, None)


_PATH_HANDLER_HEAD_RE = re.compile(r"^\s*(\w+)\s*\(")


def _extract_handler_name(vuln: Any, gn_method: str | None) -> str | None:
    """handler 名：GN 轨 source 第三段优先；LLM 轨 path 首 token 'handleXxx(req) → …'。"""
    if gn_method:
        return gn_method
    p = getattr(vuln, "path", None)
    if p:
        m = _PATH_HANDLER_HEAD_RE.match(p)
        if m:
            return m.group(1)
    return None


@dataclass
class PartialSpec:
    """确定性提取的部分 PoC spec（inj/xss/ssrf 分层组装中间结构）。

    route/witness 任一缺失 → needs_gap_fill=True，归入按 controller 文件分组的 LLM 补缺。
    wp 是 parse_witness 产物（G1）；source_file 是 LLM 轨 '@ file:line'（G5 file_key=None 修正）。
    """
    vuln: Any
    vuln_class: str
    band: ConfidenceBand
    param_name: str | None
    placement: str            # "query" | "body"
    controller_file: str | None
    method: str | None
    path: str | None
    witness: str | None       # 兼容旧签名：原始 witness 串（wp.raw 语义）
    wp: "WitnessParse" = field(default_factory=WitnessParse)
    source_file: str | None = None

    @property
    def needs_gap_fill(self) -> bool:
        return not self.method or not self.path or not self.wp.has_payload


def _extract_deterministic(
    vuln: Any, vuln_class: str, endpoints: dict, band: ConfidenceBand,
    route_index: "RouteIndex | None" = None,
) -> PartialSpec:
    """从 vuln 确定性提取 PartialSpec（不调 LLM）。缺 route/witness 时 needs_gap_fill=True。

    G3 统一路径（spec §3.2）：derive_method_path 提不出路由时依次
    witness 请求行（G1）→ RouteIndex join（entry_points）兜底；都不行才 gapped。
    """
    method, path = derive_method_path(vuln)
    source = getattr(vuln, "source", None)
    param = extract_param_name(source) or _extract_body_param(source)
    gn_param, gn_file, gn_method, gn_line = extract_gn_location(source)
    if not param and gn_param:
        param = gn_param
    wp = parse_witness(getattr(vuln, "witness_payload", None))
    placement = _infer_placement(vuln, vuln_class)
    src_file, src_line = _extract_source_location(source, getattr(vuln, "path", None))
    # 路由 fallback 1：witness 请求行形态自带 method/path（hk 实证）
    if (not method or not path) and wp.method and wp.path:
        method = method or wp.method
        path = path or wp.path
    # 路由 fallback 2：RouteIndex join（file+handler / 行号邻近 / stem 段匹配）
    if (not method or not path) and route_index is not None:
        rm, rp = route_index.resolve(
            file=gn_file or src_file, handler=_extract_handler_name(vuln, gn_method),
            line=gn_line if gn_file else src_line, placement=placement)
        method = method or rm
        path = path or rp
    return PartialSpec(
        vuln=vuln, vuln_class=vuln_class, band=band, param_name=param,
        placement=placement, controller_file=gn_file or src_file,
        method=method, path=path, witness=wp.raw, wp=wp,
        source_file=src_file,
    )


def _assemble(partial: PartialSpec, gap: dict | None, endpoints: dict) -> HttpRequestSpec | None:
    """用确定性 partial + LLM gap-fill({http_method,route_path,witness_payload}) 组装最终 spec。

    route 补回后重查 recon endpoints 得 auth_state。无 gap/缺 witness → 骨架 + 标注。
    返回 None = LLM 明确跑过（gap 非 None）却四路 route 信号全无 → 纯非 HTTP 入口 →
    调用方 skip（PoC 本就是 HTTP 请求包，非 HTTP 入口天然无 PoC）。gap=None（LLM 不可用
    /未返回该 ID）保守降级骨架，不 skip——未必非 HTTP，可能只是没跑成。

    G1：witness 统一经 parse_witness 消费——values 多参数直接落位（query/body 按
    placement），raw 单参数值走 _assemble_param；note 合并进 spec.note。
    """
    g = gap or {}
    # 不预判定——GitNexus 轨 source 是代码位置形态时 partial.method/path 为 None 但 route
    # 在 controller 文件里（LLM gap-fill 能补出），故必须等 gap-fill 结果出来再判。
    gap_route = g.get("route_path")
    has_route = bool(
        partial.method or partial.path
        or getattr(partial.vuln, "endpoint", None)
        or gap_route
    )
    if gap is not None and not has_route:
        return None  # LLM 明确补不出 route → 纯非 HTTP 入口，skip
    method = partial.method or (g.get("http_method") or "GET")
    path = partial.path or (gap_route or "/")
    wp = partial.wp if partial.wp.has_payload else parse_witness(g.get("witness_payload"))
    info = find_endpoint_info(endpoints, path)
    auth_st = derive_auth_state(info)
    spec = HttpRequestSpec(
        method=str(method).upper(), path=path,
        headers=auth_header(auth_st, info), auth_state=auth_st,
        confidence_band=partial.band,
        source_id=getattr(partial.vuln, "ID", ""), vuln_class=partial.vuln_class,
    )
    notes = [wp.note] if wp.note else []
    if not wp.has_payload:
        spec.note = "请求形态未推断（缺 witness），需手工补全 body/参数"
        return spec
    # 参数位：LLM param_location 优先，其次 partial.placement（_extract_deterministic
    # 的 _infer_placement 确定性信号 + 类兜底，与历史模板同一套逻辑）。
    # ssrf open-redirect 恒 query（302 跳转参数，现状保留）。
    gap_loc = str(g.get("param_location") or "").strip().lower()
    placement = gap_loc if gap_loc in ("query", "body") else partial.placement
    if partial.vuln_class == "ssrf" and _is_open_redirect(partial.vuln):
        placement = "query"
    param = _assemble_param(partial)
    if wp.values:
        # witness 自带参数名（请求行 query / 参数串形态）——参数集直接落位
        if placement == "body":
            if spec.method == "GET":
                spec.method = "POST"
            spec.body = _build_body_from_values(wp.values, g.get("body_template")) \
                or _build_body_from_gap(param, wp.values.get(param, ""), g.get("body_template"))
        else:
            spec.query = dict(wp.values)
    elif placement == "body":
        if spec.method == "GET":
            spec.method = "POST"
        spec.body = _build_body_from_gap(param, wp.raw or "", g.get("body_template"))
    else:
        spec.query = {param: wp.raw or ""}
    if notes:
        spec.note = "；".join(notes) if not spec.note else f"{spec.note}；{'；'.join(notes)}"
    return spec


def _build_body_from_values(values: dict[str, str], template: Any) -> str | None:
    """witness 参数串 → body：body_template（dict 原型）里同名键替换，其余保留；
    无/不识别 template → form 串拼接（'a=1&b=2'）。"""
    if isinstance(template, str) and template.strip():
        try:
            loaded = json.loads(template)
        except (json.JSONDecodeError, ValueError):
            loaded = None
        if isinstance(loaded, dict):
            template = loaded
    if isinstance(template, dict):
        data = dict(template)
        for k in values:
            if k in data:
                data[k] = values[k]
        return _coerce_request_body(data)
    return "&".join(f"{k}={v}" for k, v in values.items())


def _assemble_param(partial: "PartialSpec") -> str:
    """组装时用的参数名（对齐 build_template_spec 各类兜底链）。"""
    if partial.vuln_class == "ssrf":
        if _is_open_redirect(partial.vuln):
            return partial.param_name or "next"
        return (getattr(partial.vuln, "vulnerable_parameter", None)
                or partial.param_name or "url")
    return partial.param_name or (
        "id" if partial.vuln_class == "injection" else "q")


def _build_body_from_gap(param: str, witness: str, template: Any) -> str:
    """LLM body_template（dict/JSON-str 原型或 form-str）注入 witness → 请求体串。

    dict（或可 json.loads 的 str）：param 键存在则替换其值为 witness，其余键保留
    作 body 上下文，_coerce_request_body 归一为 JSON 串；form str：'param=xxx'
    段替换为 'param=witness'；无/不识别 template → form 兜底 'param=witness'。
    """
    if isinstance(template, str) and template.strip():
        try:
            loaded = json.loads(template)
        except (json.JSONDecodeError, ValueError):
            loaded = None
        if isinstance(loaded, dict):
            template = loaded
    if isinstance(template, dict):
        data = dict(template)
        if param in data:
            data[param] = witness
        return _coerce_request_body(data) or f"{param}={witness}"
    if isinstance(template, str) and template.strip():
        t = template.strip()
        # 左边界断言防误中后缀同名的参数（param=id 不该命中 'uid=1'）
        m = re.search(rf"(?<![A-Za-z0-9_]){re.escape(param)}=([^&]*)", t)
        if m:
            return t[:m.start()] + f"{param}={witness}" + t[m.end():]
        return f"{t}&{param}={witness}" if "=" in t else f"{param}={witness}"
    return f"{param}={witness}"


def _group_by_controller_file(
    partials: list["PartialSpec"], cap: int = 8
) -> list[tuple[str | None, list["PartialSpec"]]]:
    """按 controller_file 聚合待补 PartialSpec，每组 ≤ cap，超出按 cap 拆分多次。

    无 controller_file（提取不到）→ fallback 桶 key=None。
    cap 由 env SUPERNOVA_POC_GROUP_CAP 覆盖（默认 8）。
    """
    buckets: dict[str | None, list["PartialSpec"]] = {}
    for p in partials:
        buckets.setdefault(p.controller_file, []).append(p)
    out: list[tuple[str | None, list["PartialSpec"]]] = []
    for f, ps in buckets.items():
        for i in range(0, len(ps), cap):
            out.append((f, ps[i:i + cap]))
    return out

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


GAPFILL_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ID": {"type": "string"},
                    "http_method": {"type": ["string", "null"],
                                    "enum": ["GET", "POST", "PUT", "DELETE", "PATCH", None]},
                    "route_path": {"type": ["string", "null"]},
                    "witness_payload": {"type": ["string", "null"]},
                    # 参数位 + body 原型（修「injection/xss 恒 query」：
                    # req.body/@RequestBody 参数的 witness 必须放 body）
                    "param_location": {"type": ["string", "null"],
                                       "enum": ["query", "body", None]},
                    "body_template": {"type": ["string", "object", "null"]},
                },
                "required": ["ID"],
            },
        }
    },
    "required": ["items"],
}


def build_llm_prompt(vuln: Any, vuln_class: str, host: str, recon_ctx: dict) -> str:
    fields = {f: getattr(vuln, f) for f in _LLM_RICH_FIELDS if getattr(vuln, f, None)}
    return (
        f"You are reconstructing a replayable HTTP PoC for a confirmed {vuln_class} vulnerability.\n\n"
        f"Target host: {host}\n"
        f"Vulnerability fields:\n{json.dumps(fields, ensure_ascii=False, indent=2)}\n"
        f"Recon endpoint context:\n{json.dumps(recon_ctx, ensure_ascii=False, indent=2)}\n\n"
        "Output a JSON object describing the HTTP request shape to reproduce this vulnerability. "
        "Use witness_payload as the attack value. Fill method/path/query/body. "
        "Do NOT include the Authorization/Cookie auth header (added separately). "
        "If multi-step, put each step object in `steps`. Output JSON only."
    )


async def llm_fill_gap(
    vuln: Any, vuln_class: str, host: str, recon_ctx: dict, *,
    repo_path: str, api_key: str | None = None, model_tier: str = "medium",
    provider_config: dict | None = None,   # P3c 阶段 1：穿线下传 run_claude_prompt
) -> dict | None:
    """富信息 LLM 补缺口。失败/不可用返回 None（调用方退纯模板+标注）。"""
    prompt = build_llm_prompt(vuln, vuln_class, host, recon_ctx)
    try:
        # PoC 是"输出 JSON"的单次结构化任务，不需要 200-turn 完整 agent
        # （默认背着 CLAUDE_MAX_TURNS/SUPERNOVA_OPENAI_MAX_TURNS=200 跑，单条 PoC 可能
        # 多轮空转拖慢总时长，N 条串行 = 体感"卡住无输出"）。限 10 轮兜底，
        # JSON-only prompt 正常 1-2 轮即返回。env SUPERNOVA_POC_MAX_TURNS 可调。
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=repo_path or "/tmp/poc-gen",
            model_tier=model_tier,
            structured_output_schema=LLM_REQUEST_SCHEMA,
            api_key=api_key,
            provider_config=provider_config,   # P3c 阶段 1
            # 默认 10:原 50 让单 PoC 跑满 4-5min（GLM 对 HTTP schema 结构化输出
            # 反复不合规），N 个串行易超 start_to_close_timeout 被 retry 放大
            # （2026-07-10 NodeGoat 实测）。JSON-only 正常 1-2 轮即返回，10 足容错。
            max_turns=int(os.getenv("SUPERNOVA_POC_MAX_TURNS", "10")),
        )
    except Exception:
        return None
    if not getattr(result, "success", False) or not getattr(result, "structured_output", None):
        return None
    return result.structured_output


def _build_gapfill_prompt(file_key: str | None, partials: list["PartialSpec"], recon_ctx: dict) -> str:
    # 上下文增厚（spec R5 后续）：原来每条只给 {ID,param,class,evidence[:300]}，
    # witness 只能产通用 payload。现在给齐 sink/source/slot/代码位置/假设/完整
    # evidence（ssrf 有 vulnerable_code_location/exploitation_hypothesis，inj/xss
    # 自动为 None 跳过），并要求 LLM 判参数位 + 给 body 原型。
    # G5（spec §3.8）：file_key=None 时逐条给 source_file（不再自相矛盾
    # "Handler file: unknown … Read that file"）；recon_ctx 为空时省略该 section。
    items_desc = json.dumps([
        {k: v for k, v in {
            "ID": getattr(p.vuln, "ID", ""),
            "param": p.param_name,
            "vuln_class": p.vuln_class,
            "source": getattr(p.vuln, "source", None) or None,
            "source_file": (p.controller_file or p.source_file) or None,
            "sink_call": (getattr(p.vuln, "sink_call", None)
                          or getattr(p.vuln, "sink_function", None) or None),
            "slot_type": getattr(p.vuln, "slot_type", None) or None,
            "vulnerable_code_location": getattr(p.vuln, "vulnerable_code_location", None) or None,
            "exploitation_hypothesis": getattr(p.vuln, "exploitation_hypothesis", None) or None,
            "mismatch_reason": getattr(p.vuln, "mismatch_reason", None) or None,
            "evidence_chain": (getattr(p.vuln, "evidence_chain", None) or "")[:1000] or None,
        }.items() if v is not None}
        for p in partials
    ], ensure_ascii=False)
    if file_key:
        file_line = f"Handler file: {file_key}\nRead that file and find each handler method's HTTP route "
    else:
        file_line = ("For each item, read its \"source_file\" (if given) and find that handler's HTTP route ")
    recon_section = (f"Recon endpoint context:\n{json.dumps(recon_ctx, ensure_ascii=False)}\n\n"
                     if recon_ctx else "")
    return (
        f"You are reconstructing HTTP request shapes for confirmed vulnerabilities.\n\n"
        f"{file_line}"
        f"(@PostMapping / router.get / @app.route …) and a minimal witness payload.\n\n"
        f"Vulnerabilities to fill:\n{items_desc}\n\n"
        f"{recon_section}"
        'For each item also decide "param_location": "query" if the tainted param '
        'is read from the query string, "body" if from the request body. '
        'When body, also give "body_template": the JSON object prototype of that '
        "endpoint's body (tainted param as a key with a placeholder value) or a "
        "form string like 'a=1&b=2'.\n\n"
        f'Output JSON {{"items":[{{"ID","http_method","route_path",'
        f'"witness_payload","param_location","body_template"}}]}}. Output JSON only.'
    )


async def llm_fill_gaps(
    file_key: str | None, partials: list["PartialSpec"], *, recon_ctx: dict,
    repo_path: str, api_key: str | None = None, model_tier: str = "medium",
    provider_config: dict | None = None,   # P3c 阶段 1：穿线下传 run_claude_prompt
) -> dict[str, dict]:
    """一个 controller 文件组一次 LLM 调用,返回 {ID: {http_method,route_path,witness_payload}}。

    G5（spec §3.8，治 kol gap-fill 0/3 全败）：unparseable/缺 items 时有界重试一次
    （prompt 加 JSON-only 强化；env SUPERNOVA_POC_GAPFILL_RETRIES 默认 1，对齐
    fd203e12 chain_verdict 重试模式）。重试耗尽 → {}(调用方对缺 gap 的条目降级骨架)。
    """
    retries = max(0, int(os.getenv("SUPERNOVA_POC_GAPFILL_RETRIES", "1")))
    prompt = _build_gapfill_prompt(file_key, partials, recon_ctx)
    items: list | None = None
    for attempt in range(1 + retries):
        try:
            result = await run_claude_prompt(
                prompt=prompt,
                repo_path=repo_path or "/tmp/poc-gen",
                model_tier=model_tier,
                # runner 现状:output_format 是主参(structured_output_schema 为别名,见 runner.py:139)。
                # chain_verdict 的 _make_verdict_llm_client 也走 output_format,此处对齐。
                output_format=GAPFILL_OUTPUT_SCHEMA,
                api_key=api_key,
                provider_config=provider_config,   # P3c 阶段 1
                max_turns=int(os.getenv("SUPERNOVA_POC_MAX_TURNS", "10")),
            )
        except Exception:
            return {}  # 网络/引擎异常：runner 内部已有重试，这里不叠加
        if getattr(result, "success", False) and getattr(result, "structured_output", None):
            got = result.structured_output.get("items") or []
            if got:
                items = got
                break
        if attempt < retries:
            prompt = (prompt + "\n\nIMPORTANT: Your previous reply was not valid. "
                       "Reply with ONLY the JSON object, no prose, no markdown fences.")
    if not items:
        return {}
    out: dict[str, dict] = {}
    for it in items:
        vid = it.get("ID")
        if vid:
            out[vid] = {
                "http_method": it.get("http_method"),
                "route_path": it.get("route_path"),
                "witness_payload": it.get("witness_payload"),
                "param_location": it.get("param_location"),
                "body_template": it.get("body_template"),
            }
    return out


def _trim_recon_ctx(endpoints: dict, partials: list["PartialSpec"]) -> dict:
    """G5（spec §3.8）recon_ctx 裁剪：按组内 source_file/controller_file basename
    stem 匹配端点 path 段（contributions.js ↔ /contributions/…），只保留命中端点；
    无命中/无文件信息 → {}（prompt 省略端点 section，不再全量灌入）。
    """
    if not endpoints:
        return {}
    stems = set()
    for p in partials:
        f = p.controller_file or p.source_file
        if f:
            base = _file_basename(f)
            stem = base.rsplit(".", 1)[0] if "." in base else base
            if stem:
                stems.add(stem.lower())
    if not stems:
        return {}
    out = {}
    for ep, info in endpoints.items():
        segs = [s.lower() for s in ep.strip("/").split("/") if s]
        if any(s in segs for s in stems):
            out[ep] = info
    return out


async def _batch_fill_gaps(
    partials: list["PartialSpec"], *, endpoints: dict, repo_path: str,
    api_key: str | None = None, model_tier: str = "medium",
    provider_config: dict | None = None,   # P3c 阶段 1：透传 llm_fill_gaps
    semaphore: "asyncio.Semaphore | None" = None,   # G7：组并行共享 cap
) -> dict[str, dict]:
    """编排:分组 + 组并行调 llm_fill_gaps,合并 {ID: gap}。失败的组其条目无 gap(后降级)。

    G5：recon_ctx 按组内文件 stem 裁剪（不再全量灌入端点表）。
    G7：各组 asyncio.gather 并行（与 auth 共用 semaphore cap）。
    """
    cap = int(os.getenv("SUPERNOVA_POC_GROUP_CAP", "8"))
    groups = _group_by_controller_file(partials, cap=cap)

    async def _one(file_key: str | None, group_partials: list["PartialSpec"]) -> dict[str, dict]:
        recon_ctx = _trim_recon_ctx(endpoints, group_partials)
        async def _call() -> dict[str, dict]:
            return await llm_fill_gaps(
                file_key, group_partials, recon_ctx=recon_ctx,
                repo_path=repo_path, api_key=api_key, model_tier=model_tier,
                provider_config=provider_config)   # P3c 阶段 1
        if semaphore is not None:
            async with semaphore:
                return await _call()
        return await _call()

    results = await asyncio.gather(
        *(_one(f, gp) for f, gp in groups), return_exceptions=True)
    gapmap: dict[str, dict] = {}
    for (file_key, _), res in zip(groups, results):
        if isinstance(res, BaseException):  # 单组失败不阻塞其余
            logger.warning("poc: llm_fill_gaps failed for %s: %s", file_key, res)
        elif res:
            gapmap.update(res)
    return gapmap


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

# G2（spec §3.5）：白盒 CONFIRMED 是静态判定（无重放证据），文案不再声称「可复现」。
BAND_FULL_WHITEBOX = {
    ConfidenceBand.CONFIRMED: "已确认（静态判定）",
    ConfidenceBand.HIGH: "高置信",
    ConfidenceBand.SUSPECTED: "疑似待验证",
}

_AUTH_LABEL = {
    AuthState.NONE: "无需登录",
    AuthState.REQUIRED: "需登录",
    AuthState.UNKNOWN: "未知",
}


def _placeholder_block() -> str:
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


def _detail_section(vuln_class: str, vuln: Any, spec: HttpRequestSpec, host: str,
                    track: str = "blackbox") -> str:
    band_full = BAND_FULL_WHITEBOX if track == "whitebox" else BAND_FULL
    band_mark = {"confirmed": "✓", "high": "●", "suspected": "⚠"}[spec.confidence_band.value]
    auth = _AUTH_LABEL.get(spec.auth_state, "未知")
    note = f"\n> {spec.note}" if spec.note else ""
    lines = [
        f"### {band_mark} {spec.source_id} · {vuln_class} @ {spec.method} {spec.path}",
        f"**置信度：{band_full[spec.confidence_band]}** ｜ 认证：{auth} ｜ 来源：{getattr(vuln, 'merge_source', '-')}{note}",
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


def render_poc_md(entries, host: str, track: str) -> str:
    """渲染 PoC 集合 Markdown（概览表 + 详细 curl/Burp）。

    Args:
        entries: list[tuple[vuln_class, vuln, HttpRequestSpec | list[HttpRequestSpec]]]
        host: 目标 host（可能为占位符）
        track: "whitebox" 或 "blackbox"

    Returns:
        完整 PoC 文档 Markdown 字符串
    """
    track_cn = "白盒" if track == "whitebox" else "黑盒"
    entries = merge_duplicate_requests(entries)  # G8：相同请求去重（渲染前关卡）
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
        f"{_placeholder_block()}\n"
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
            detail.append(_detail_section(vuln_class, vuln, s, host, track).replace(
                f" · {vuln_class} @ ", f"{heading} · {vuln_class} @ "))
            detail.append("\n---\n")
    return header + "\n".join(overview) + "\n" + "\n".join(detail)


def empty_poc_md(track: str) -> str:
    """空表兜底：无 HTTP 入口漏洞（拼不出 HTTP PoC）时调用。"""
    track_cn = "白盒" if track == "whitebox" else "黑盒"
    return f"# 可利用漏洞 PoC 集合（{track_cn}）\n\n本次扫描无可生成 PoC 的 HTTP 漏洞，未生成 PoC。\n"


# Task 7: generate() 主流程（编排 + 过滤 + LLM 仲裁 + 降级 + 读写）

logger = logging.getLogger(__name__)
_POC_FILENAME = "exploitable_poc_collection.md"
_POC_CHECKPOINT_FILENAME = ".poc_checkpoint.json"
_CKPT_VERSION = 2  # G8（spec §5）：v2 起 _load_checkpoint 校验 version+track


def _ckpt_path(deliverables_dir: Path) -> Path:
    # tiering：checkpoint 是管线状态 → 桶内 intermediate/（atomic 写自动建目录）。
    from supernova_core.utils.paths import intermediate_path
    return intermediate_path(deliverables_dir, _POC_CHECKPOINT_FILENAME)


def _load_checkpoint(deliverables_dir: Path, track: str | None = None) -> dict:
    """读 sidecar checkpoint。损坏/缺失/version 不符/track 不符 → 返回空（从头跑）。

    G8（spec §5，修 07-22 实现偏差②）：v2 起强制校验 version==2 且 track 匹配——
    旧 v1（本次修复前的错误 spec）自动失效，修复对存量 deliverables 重跑即生效。
    """
    from supernova_core.utils.paths import resolve_intermediate
    p = resolve_intermediate(deliverables_dir, _POC_CHECKPOINT_FILENAME)
    if p is None:
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != _CKPT_VERSION:
            return {}
        if track is not None and data.get("track") != track:
            return {}
        return data.get("completed", {}) if isinstance(data.get("completed"), dict) else {}
    except Exception:
        return {}


def _spec_to_ckpt(spec: HttpRequestSpec | list[HttpRequestSpec]) -> Any:
    """HttpRequestSpec(或 list)序列化为 checkpoint 可存 dict。"""
    if isinstance(spec, list):
        return [_spec_to_ckpt(s) for s in spec]
    return {
        "method": spec.method, "path": spec.path, "query": spec.query,
        "headers": spec.headers, "body": spec.body, "auth_state": spec.auth_state.value,
        "confidence_band": spec.confidence_band.value, "source_id": spec.source_id,
        "vuln_class": spec.vuln_class, "note": spec.note, "steps": None,
    }


def _spec_from_ckpt(raw: Any) -> HttpRequestSpec | list[HttpRequestSpec] | None:
    """从 checkpoint dict 还原 HttpRequestSpec(authz 成对存为 list,一并还原)。

    与 _spec_to_ckpt 对称:dict→单 spec,list→list[spec]。损坏结构 → None(调用方跳过)。
    """
    if isinstance(raw, list):
        items = [_spec_from_ckpt(r) for r in raw]
        return items if items and all(s is not None for s in items) else None
    if not isinstance(raw, dict):
        return None
    try:
        return HttpRequestSpec(
            method=raw.get("method", "GET"), path=raw.get("path", "/"),
            query=raw.get("query", {}), headers=raw.get("headers", {}),
            body=raw.get("body"),
            auth_state=AuthState(raw.get("auth_state", "unknown")),
            confidence_band=ConfidenceBand(raw.get("confidence_band", "suspected")),
            source_id=raw.get("source_id", ""), vuln_class=raw.get("vuln_class", ""),
            note=raw.get("note"),
        )
    except Exception:
        return None


def _write_checkpoint(deliverables_dir: Path, track: str,
                      completed: dict[str, dict]) -> None:
    """原子写 checkpoint(临时文件 + os.replace)。"""
    p = _ckpt_path(deliverables_dir)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)  # intermediate/ 桶可能尚未建（测试/首写）
        tmp.write_text(json.dumps(
            {"version": _CKPT_VERSION, "track": track, "completed": completed},
            ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        logger.warning("poc: checkpoint write failed (non-blocking)")


# G8（spec §3.7）：相同请求去重合并 —— 渲染前按规范化请求分组。

def _request_key(spec: HttpRequestSpec) -> tuple:
    return (
        spec.method, spec.path,
        tuple(sorted(spec.query.items())),
        spec.body,
        tuple(sorted(spec.headers.items())),
    )


def merge_duplicate_requests(
    entries: list[tuple[str, Any, "HttpRequestSpec | list[HttpRequestSpec]"]],
) -> list[tuple[str, Any, "HttpRequestSpec | list[HttpRequestSpec]"]]:
    """按 (method, path, 规范化 query, body, headers) 分组去重（authz 成对以整个 list 为 key）。

    组内 >1 → 合并一节：detail/概览一份，ID 逗连（INJ-VULN-01/02/03）。
    NodeGoat 实证唯一率 57–72%（GN/LLM 轨同请求重复），目标 100% 唯一。
    """
    groups: dict[tuple, list[int]] = {}
    for idx, (_, _, spec_or_list) in enumerate(entries):
        specs = spec_or_list if isinstance(spec_or_list, list) else [spec_or_list]
        key = tuple(_request_key(s) for s in specs)
        groups.setdefault(key, []).append(idx)
    if all(len(v) == 1 for v in groups.values()):
        return entries  # 无重复，原样返回（含顺序）
    out: list[tuple[str, Any, "HttpRequestSpec | list[HttpRequestSpec]"]] = []
    for key, idxs in groups.items():
        first_vc, first_vuln, first_spec = entries[idxs[0]]
        if len(idxs) == 1:
            out.append(entries[idxs[0]])
            continue
        ids: list[str] = []
        for i in idxs:
            specs = entries[i][2] if isinstance(entries[i][2], list) else [entries[i][2]]
            for s in specs:
                if s.source_id and s.source_id not in ids:
                    ids.append(s.source_id)
        joined = _join_ids(ids)
        if isinstance(first_spec, list):
            for s in first_spec:
                s.source_id = joined
        else:
            first_spec.source_id = joined
        note = first_spec[0].note if isinstance(first_spec, list) else first_spec.note
        merged_note = "；".join(filter(None, [note, f"同请求合并：{joined}"]))
        if isinstance(first_spec, list):
            first_spec[0].note = merged_note
        else:
            first_spec.note = merged_note
        out.append((first_vc, first_vuln, first_spec))
    return out


_ID_NUM_TAIL_RE = re.compile(r"^(.*?)(\d+)$")


def _join_ids(ids: list[str]) -> str:
    """同前缀编号 ID 逗连：'INJ-VULN-01','INJ-VULN-02' → 'INJ-VULN-01/02'；
    前缀不同 → 全 ID 直接 '/' 连（不误并）。"""
    if len(ids) <= 1:
        return ids[0] if ids else ""
    ms = [_ID_NUM_TAIL_RE.match(s) for s in ids]
    if all(m and m.group(1) == ms[0].group(1) for m in ms):
        return ms[0].group(1) + "/".join(m.group(2) for m in ms)
    return "/".join(ids)


def _resolve_input(deliverables_dir: Path, filename: str) -> Path | None:
    """先在 track 目录找，不存在回退 parent（兼容老平铺 session）。

    tiering（spec 2026-08-18）：queue/verdicts 等中间产物落 track/intermediate/，
    resolve_intermediate 已含 intermediate/ 优先 + track 顶层兜底，parent 再兜
    老平铺 session 根。
    """
    from supernova_core.utils.paths import resolve_intermediate
    p = resolve_intermediate(deliverables_dir, filename)
    if p is not None:
        return p
    parent = deliverables_dir.parent / filename
    if parent.exists():
        return parent
    return None


def _load_accepted_ids(deliverables_dir: Path, vuln_class: str) -> set[str]:
    p = _resolve_input(deliverables_dir, f"{vuln_class}_exploit_verdicts.json")
    if not p:
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data.get("accepted_ids", []))
    except Exception:
        return set()


def _coerce_request_body(raw: Any) -> str | None:
    """LLM structured_output 不可靠（GLM 无 strict），body 可能返回 dict/list 而非 schema
    声明的 str。归一化为 JSON 字符串以保 spec.body 类型不变量 str|None，否则 to_burp_raw
    的 spec.body.lstrip() 对 dict 崩（2026-07-21 sentinel_dashboard 实测，整个 PoC 报告丢失）。
    """
    if isinstance(raw, (dict, list)):
        return json.dumps(raw, ensure_ascii=False)
    if isinstance(raw, str):
        return raw
    return None


def _coerce_str_dict(raw: Any) -> dict[str, str]:
    """LLM structured_output 不可靠（GLM 无 strict），query/headers 可能返回 str 而非
    schema 声明的 object。归一化为 dict[str,str]，守 spec 类型不变量，避免
    'str' object has no attribute 'items'（2026-07-22 sentinel_dashboard INJ-GN-08 实测）。
    """
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        if s[:1] in ("{", "["):
            try:
                p = json.loads(s)
                return {str(k): str(v) for k, v in p.items()} if isinstance(p, dict) else {}
            except Exception:
                return {}
        out: dict[str, str] = {}
        for pair in s.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                out[k.strip()] = v.strip()
        return out
    return {}


def _spec_from_llm_guess(guess: dict, vuln: Any, vuln_class: str, band: ConfidenceBand) -> HttpRequestSpec:
    return HttpRequestSpec(
        method=(guess.get("method") or "GET").upper(),
        path=guess.get("path") or "/",
        query=_coerce_str_dict(guess.get("query")),
        headers=_coerce_str_dict(guess.get("headers")),
        body=_coerce_request_body(guess.get("body")),
        auth_state=AuthState.UNKNOWN,
        confidence_band=band,
        source_id=getattr(vuln, "ID", ""),
        vuln_class=vuln_class,
    )


# vuln_class → 显示标签，对齐 display 层 _AGENT_PREFIXES 风格（[Injection]/[XSS]…）。
_POC_CLASS_TAG: dict[str, str] = {
    "injection": "[Injection]",
    "xss": "[XSS]",
    "auth": "[Auth]",
    "authz": "[Authz]",
    "ssrf": "[SSRF]",
}


async def _poc_progress(body: str) -> None:
    """PoC 进度经 audit_session.log_info 发 InfoEvent（取代裸 print）。

    spec 组件 3：activity 内能拿 get_audit_session()，log_info 是官方取代裸 logging 的
    通道（session.py docstring："Replaces bare logger.warning/info in workflow threads"），
    渲染对齐 [INFO ]。无 session 上下文（测试/standalone）时 NullAuditSession.log_info no-op
    安全。body 为进度正文（renderer 已含 INFO 标签，不重复符号）。
    """
    await get_audit_session().log_info(body)


class PoCGenerator:
    @staticmethod
    async def generate(
        deliverables_dir: Path,
        vuln_classes: list[str],
        target_url: str | None,
        track: str,
        *,
        repo_path: str | None = None,
        api_key: str | None = None,
        model_tier: str = "medium",
        provider_config: dict | None = None,   # P3c 阶段 1：穿线下传 _build_entry/_batch_fill_gaps
    ) -> Path | None:
        # 开关：SUPERNOVA_SKIP_POC_REPORT=1 跳过整个 PoC 生成（默认 0=生成 PoC）。
        # 报告增强本就非关键路径，token 紧张或暂不需要 PoC 时设 1 秒过，不阻塞主报告。
        if os.getenv("SUPERNOVA_SKIP_POC_REPORT", "0") == "1":
            logger.info("poc: SUPERNOVA_SKIP_POC_REPORT=1, 跳过 PoC 生成")
            await _poc_progress("SUPERNOVA_SKIP_POC_REPORT=1, 跳过 PoC 生成")
            return None

        host = resolve_host(target_url)
        recon_path = _resolve_input(deliverables_dir, "recon_deliverable.md")
        endpoints = parse_recon_endpoints(recon_path) if recon_path else {}
        # G3：RouteIndex —— entry_points.json（GitNexus 确定性入口裁决）join 补路由。
        # 报告层消费确定性产物（与 parse_recon_endpoints 同档，不喂判定轨 prompt）。
        # 黑盒 track / entry_points 缺失 → 空 index（resolve 全 miss，不劣于现状）。
        ep_path = _resolve_input(deliverables_dir, "entry_points.json")
        route_index = RouteIndex([])
        if ep_path:
            try:
                _eps = json.loads(ep_path.read_text(encoding="utf-8"))
                route_index = RouteIndex(_eps.get("adjudicated_entry_points") or [])
            except Exception as exc:
                logger.warning("poc: entry_points unreadable: %s", exc)

        # 先收集所有 externally_exploitable 漏洞，便于打 (i/N) 进度行。
        # queue 解析快（小 json），预扫一遍换可读进度，值得。
        items: list[tuple[str, Any, Any]] = []  # (vuln_class, vuln, accepted_ids)
        for vc in vuln_classes:
            queue_path = _resolve_input(deliverables_dir, f"{vc}_exploitation_queue.json")
            if not queue_path:
                continue
            try:
                parsed = VulnerabilityQueue.parse_lenient(queue_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("poc: queue %s unreadable: %s", vc, exc)
                continue
            if parsed.warnings:
                logger.warning("poc: queue %s lenient: %s", vc, parsed.warnings)
            accepted = _load_accepted_ids(deliverables_dir, vc)
            for v in parsed.queue.vulnerabilities:
                # ee 不再当 PoC 门控（对齐 prompts/vuln-*.txt 契约：
                # "externally_exploitable is a REACHABILITY TAG, not an admission gate"）。
                # 所有 vulnerable 漏洞都进生成流程；纯非 HTTP 入口在 _assemble 阶段 skip。
                items.append((vc, v, accepted))

        total = len(items)
        track_cn = "白盒" if track == "whitebox" else "黑盒"
        await _poc_progress(f"{track_cn} PoC: {total} 个可生成 PoC 的漏洞")

        entries: list[tuple[str, Any, HttpRequestSpec | list[HttpRequestSpec]]] = []
        entries_by_idx: dict[int, tuple[str, Any, HttpRequestSpec | list[HttpRequestSpec]]] = {}
        # inj/xss/ssrf 的待补项(模板未命中),收集后按文件分组批量补缺
        gapped: list[tuple[int, "PartialSpec"]] = []
        # Fix B:断点续传 — 读 checkpoint,reuse 已完成项,retry 不从零重来
        # G8：v2 校验 version+track（v1/异 track 丢弃，修复对存量重跑生效）
        ckpt_completed = _load_checkpoint(deliverables_dir, track)
        ckpt_done_ids = set(ckpt_completed.keys())

        # G7（spec §4）并发基础设施：auth per-item 与 gap-fill 组共用 cap；
        # per-call 超时治单条 5m12s 空转白烧（NodeGoat 实测 auth 占阶段 82%）。
        semaphore = asyncio.Semaphore(max(1, int(os.getenv("SUPERNOVA_POC_CONCURRENCY", "3"))))
        auth_timeout_s = float(os.getenv("SUPERNOVA_POC_AUTH_TIMEOUT_S", "180"))
        ckpt_lock = asyncio.Lock()

        # 阶段 1：预扫分拣 —— ckpt 命中 / inj/xss/ssrf 分层快速路径（0ms）/
        # authz+auth 收集为 per-item 任务（阶段 2 并行）。
        per_item: list[tuple[int, str, Any, set]] = []
        for i, (vc, v, accepted) in enumerate(items, 1):
            vid = getattr(v, "ID", "?")
            label = f"({i}/{total}) {_POC_CLASS_TAG.get(vc, f'[{vc}]')} {vid}"
            t0 = time.monotonic()
            try:
                # Fix B:断点续传 — checkpoint 命中则复用,跳过模板/LLM
                if vid in ckpt_done_ids and vid in ckpt_completed:
                    raw = ckpt_completed[vid]
                    spec = _spec_from_ckpt(raw["spec"]) if isinstance(raw, dict) else None
                    if spec is not None:
                        entries_by_idx[i] = (vc, v, spec)
                        await _poc_progress(f"{label}  复用(checkpoint)")
                        continue
                if vc in ("authz", "auth"):
                    per_item.append((i, vc, v, accepted))
                    continue
                # inj/xss/ssrf：G3 统一分层路径（spec §3.2）——确定性提取
                # （derive_method_path → witness 请求行 → RouteIndex join）
                # 完整 → _assemble 0ms；缺 route/witness → 待补桶（分组 gap-fill）。
                band = classify_confidence(v, is_accepted=(vid in accepted))
                partial = _extract_deterministic(v, vc, endpoints, band, route_index)
                if not partial.needs_gap_fill:
                    spec = _assemble(partial, None, endpoints)
                    if spec is None:
                        await _poc_progress(f"{label}  skip {format_duration(int((time.monotonic()-t0)*1000))}")
                    else:
                        lint_spec(spec)
                        entries_by_idx[i] = (vc, v, spec)
                        await _poc_progress(f"{label}  {format_duration(int((time.monotonic()-t0)*1000))}")
                else:
                    gapped.append((i, partial))
                    await _poc_progress(f"{label}  待补缺(分组) {format_duration(int((time.monotonic()-t0)*1000))}")
                # Fix B:增量写 checkpoint(分层快速路径;gapped 待补项在分组补缺后统一写)
                if i in entries_by_idx:
                    _vc, _v, _spec = entries_by_idx[i]
                    ckpt_completed[getattr(_v, "ID", str(i))] = {
                        "vuln_class": _vc, "spec": _spec_to_ckpt(_spec)}
                    _write_checkpoint(deliverables_dir, track, ckpt_completed)
            except Exception as exc:  # 单条失败不阻塞其余
                dt_ms = int((time.monotonic() - t0) * 1000)
                logger.warning("poc: build failed for %s: %s", vid, exc)
                await _poc_progress(f"{label}  — {exc} ({format_duration(dt_ms)})")

        # 阶段 2：authz/auth per-item 并行（G7）——Semaphore cap + per-call 超时。
        # authz 本身 0ms 模板，auth 是逐条 LLM（原串行是速度唯一大头）。
        if per_item:
            async def _run_item(i: int, vc: str, v: Any, accepted: set) -> None:
                vid = getattr(v, "ID", "?")
                label = f"({i}/{total}) {_POC_CLASS_TAG.get(vc, f'[{vc}]')} {vid}"
                t0 = time.monotonic()

                async def _call() -> HttpRequestSpec | list[HttpRequestSpec] | None:
                    async with semaphore:
                        return await PoCGenerator._build_entry(
                            v, vc, host, endpoints, accepted,
                            repo_path=repo_path, api_key=api_key, model_tier=model_tier,
                            provider_config=provider_config)   # P3c 阶段 1

                try:
                    spec = await asyncio.wait_for(_call(), timeout=auth_timeout_s)
                except asyncio.TimeoutError:
                    band = classify_confidence(v, is_accepted=(vid in accepted))
                    spec = _base_spec(v, vc, endpoints, band)
                    spec.note = "LLM 超时，需手工补全请求形态"
                except Exception as exc:  # 单条失败不阻塞其余
                    logger.warning("poc: build failed for %s: %s", vid, exc)
                    spec = None
                dt_ms = int((time.monotonic() - t0) * 1000)
                if spec is None:
                    await _poc_progress(f"{label}  — 失败/跳过 ({format_duration(dt_ms)})")
                    return
                for s in (spec if isinstance(spec, list) else [spec]):
                    lint_spec(s)
                entries_by_idx[i] = (vc, v, spec)
                # Fix B：并行完成即写 checkpoint（锁保护并发写盘）
                async with ckpt_lock:
                    ckpt_completed[vid] = {"vuln_class": vc, "spec": _spec_to_ckpt(spec)}
                    _write_checkpoint(deliverables_dir, track, ckpt_completed)
                await _poc_progress(f"{label}  {format_duration(dt_ms)}")

            await asyncio.gather(*(_run_item(*t) for t in per_item))

        # 分组批量补缺(GitNexus 轨缺 route/witness 的项)——G7：组并行共享 cap
        if gapped:
            await _poc_progress(f"PoC 分组补缺: {len(gapped)} 条待补")
            gapmap = await _batch_fill_gaps(
                [p for _, p in gapped], endpoints=endpoints,
                repo_path=repo_path or "/tmp/poc-gen", api_key=api_key, model_tier=model_tier,
                provider_config=provider_config,   # P3c 阶段 1
                semaphore=semaphore)
            for i, partial in gapped:
                vid = getattr(partial.vuln, "ID", "?")
                spec = _assemble(partial, gapmap.get(vid), endpoints)
                if spec is None:
                    await _poc_progress(f"{vid}  非 HTTP 入口,skip(拼不出 HTTP PoC)")
                    continue  # 纯非 HTTP 入口：不入 entries，不写 checkpoint
                lint_spec(spec)
                entries_by_idx[i] = (partial.vuln_class, partial.vuln, spec)
                ckpt_completed[vid] = {
                    "vuln_class": partial.vuln_class, "spec": _spec_to_ckpt(spec)}
            _write_checkpoint(deliverables_dir, track, ckpt_completed)
            await _poc_progress(f"PoC 分组补缺完成: {len(gapmap)}/{len(gapped)} 条补回 route+witness")

        entries = [entries_by_idx[i] for i in sorted(entries_by_idx)]

        # placeholder 块总是显示：即便 host 真实，需登录的 PoC 仍含 <AUTH_TOKEN>/<SESSION_COOKIE> 占位符，operator 需替换指引。
        md = render_poc_md(entries, host, track) if entries else empty_poc_md(track)
        out = deliverables_dir / _POC_FILENAME
        out.write_text(md, encoding="utf-8")
        await _poc_progress(f"PoC 完成: {len(entries)}/{total} 写入 {out.name}")
        return out

    @staticmethod
    async def _build_entry(vuln, vuln_class, host, endpoints, accepted, *,
                           repo_path, api_key, model_tier,
                           provider_config: dict | None = None) -> HttpRequestSpec | list[HttpRequestSpec] | None:   # P3c 阶段 1：透传 llm_fill_gap
        band = classify_confidence(vuln, is_accepted=(getattr(vuln, "ID", "") in accepted))
        template = build_template_spec(vuln, vuln_class, host, endpoints, band)
        if template is not None:
            return template
        # 模板无法处理（auth / 缺 witness_payload / 缺 path）→ 富信息 LLM
        method, path = derive_method_path(vuln)
        info = find_endpoint_info(endpoints, path)
        recon_ctx = {path or "?": info} if info else {}
        guess = await llm_fill_gap(vuln, vuln_class, host, recon_ctx,
                                   repo_path=repo_path or "/tmp/poc-gen",
                                   api_key=api_key, model_tier=model_tier,
                                   provider_config=provider_config)   # P3c 阶段 1
        if not guess:
            # LLM 不可用/失败 → 骨架 + 标注
            spec = _base_spec(vuln, vuln_class, endpoints, band)
            spec.note = "请求形态未推断（LLM 不可用），需手工补全 body/参数"
            return spec
        if guess.get("steps"):
            return [_spec_from_llm_guess(s, vuln, vuln_class, band) for s in guess["steps"]]
        return _spec_from_llm_guess(guess, vuln, vuln_class, band)
