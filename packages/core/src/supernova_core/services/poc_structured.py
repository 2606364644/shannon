# packages/core/src/supernova_core/services/poc_structured.py
"""结构化 POC 生成（spec 2026-08-26-report-generation-agent-design §4 poc / §5.3 T4③）。

与 poc_generator（curl 模板 + gap-fill → md 文档）互补的**结构化**轨道：单漏洞产
``models/report_data.PocBlock`` 形态 dict（request/preconditions/expected_response/
witness_payload + 确定性 curl/raw_http），由上层编排写回 queue entry 的
``report_poc`` 字段（queue_schemas 已加，append-only）。

职责分层（spec §5.3 升级位）：
- ``request`` **纯确定性**——endpoint/endpoints/path/source_endpoint/source 提取
  method+path（复用 poc_generator 的 extract_method_path/parse_witness/_infer_placement
  等成熟逻辑），base_url 拼接；body 由 witness_payload/affected_parameters 构造。
  提不出路由锚点（纯非 HTTP 入口，如 GN 轨纯代码位置 source 且无 witness 请求行）
  → 返回 None，调用方回退现行确定性模板路径（§5.6 降级矩阵），不硬拼 ``GET /``。
- ``expected_response`` **LLM 产**——经 ``llm_fn(prompt) -> dict | str | None`` 注入
  （上层可包装 run_claude_prompt / subagent）；不给/抛异常/产物不可理解 → None，
  确定性字段照常返回，绝不抛。
- ``preconditions`` 从 authentication_required（"true"|"false" 契约）/notes 派生。

``llm_fn`` 契约：sync callable，入参 prompt 字符串，返回 ``{"indicator", "success_criteria"?}``
dict、可 json 解析的（可含 markdown fence）字符串、或纯文本字符串（整段作 indicator）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import urlencode

from supernova_core.services.poc_generator import (
    _build_body_from_values,
    _extract_body_param,
    _infer_placement,
    _sh_quote,
    extract_gn_location,
    extract_method_path,
    extract_param_name,
    parse_witness,
    resolve_host,
)

LlmFn = Callable[[str], Any]


def _s(value: Any) -> str | None:
    """宽容取 str：queue 字段理论上 str|None，畸形（dict/bool）按缺失处理。"""
    return value if isinstance(value, str) else None


# --------------------------------------------------------------------------- #
# request 确定性构造
# --------------------------------------------------------------------------- #

_BARE_PATH_RE = re.compile(r"^/[^\s]*$")


def _route_from_field(text: str | None) -> tuple[str | None, str | None]:
    """单字段提 (method, path)：先 'METHOD /path' 形态，再裸 '/path' 形态。"""
    m, p = extract_method_path(text)
    if m:
        return m, p
    stripped = (text or "").strip().strip("`")
    if stripped and _BARE_PATH_RE.match(stripped):
        return None, stripped
    return None, None


def _extract_route(vuln: Any) -> tuple[str | None, str | None]:
    """确定性路由链：endpoint → endpoints 列表 → path → source_endpoint → source。

    对齐 derive_method_path 的字段优先级并扩 endpoints 列表（'POST /memos (write)'，
    角色注记不进 path）；witness 请求行兜底在 _build_request 里（parse_witness 产物）。
    """
    m, p = _route_from_field(_s(getattr(vuln, "endpoint", None)))
    if m or p:
        return m, p
    endpoints = getattr(vuln, "endpoints", None) or []
    if isinstance(endpoints, list):
        for ep in endpoints:
            m, p = _route_from_field(_s(ep))
            if m or p:
                return m, p
    for field in ("path", "source_endpoint", "source"):
        m, p = _route_from_field(_s(getattr(vuln, field, None)))
        if m or p:
            return m, p
    return None, None


_PARAM_ANNOTATION_RE = re.compile(r"^(.*?)\s*[（(][^()（）]*[)）]\s*$")


def _strip_param_annotation(name: str) -> str:
    """'memo (body)' → 'memo'（affected_parameters 可带来源注记）。"""
    m = _PARAM_ANNOTATION_RE.match(name.strip())
    return (m.group(1) if m else name).strip()


def _annotated_placement(vuln: Any) -> str | None:
    """affected_parameters 注记位优先：'memo (body)' → body、'q (query)' → query。"""
    for ap in (getattr(vuln, "affected_parameters", None) or []):
        if not isinstance(ap, str):
            continue
        low = ap.lower()
        if low.endswith("(body)"):
            return "body"
        if low.endswith("(query)"):
            return "query"
    return None


def _extract_param(vuln: Any) -> str | None:
    """参数名链：source 参数位 → GN source → affected_parameters → vulnerable_parameter。"""
    source = _s(getattr(vuln, "source", None))
    param = extract_param_name(source) or _extract_body_param(source)
    if not param:
        param = extract_gn_location(source)[0]
    if not param:
        for ap in (getattr(vuln, "affected_parameters", None) or []):
            if isinstance(ap, str):
                name = _strip_param_annotation(ap)
                if name:
                    return name
    if not param:
        param = _s(getattr(vuln, "vulnerable_parameter", None))
    return param or None


def _witness_text(vuln: Any) -> str | None:
    """witness 直传链：witness_payload → minimal_witness（authz 语义）。"""
    w = _s(getattr(vuln, "witness_payload", None))
    if w and w.strip():
        return w
    mw = _s(getattr(vuln, "minimal_witness", None))
    return mw if mw and mw.strip() else None


def _normalize_base_url(base_url: str | None) -> str:
    """resolve_host 复用（空 → 占位符），再补 scheme——raw_http 的 Host 依赖 netloc。"""
    host = resolve_host(base_url)
    if not re.match(r"^https?://", host, re.IGNORECASE):
        host = "http://" + host
    return host


def _content_type_for_body(body: str) -> str:
    """body 形态判 Content-Type（对齐 to_burp_raw：JSON 原型不强制 form）。"""
    return "application/json" if body.lstrip()[:1] in ("{", "[") \
        else "application/x-www-form-urlencoded"


def _build_request(vuln: Any, base_url: str | None) -> dict | None:
    """确定性构造 PocRequest 形态 dict；无路由锚点（无 path 可提）→ None。"""
    method, path = _extract_route(vuln)
    wp = parse_witness(_witness_text(vuln))
    # 路由兜底：witness 请求行自带 method/path（'POST /x?k=v'）
    if (not method or not path) and wp.method and wp.path:
        method = method or wp.method
        path = path or wp.path
    if not path:
        return None  # 纯非 HTTP 入口：无锚点不硬拼（调用方回退现行降级路径）
    placement = _annotated_placement(vuln) or _infer_placement(vuln, "")
    param = _extract_param(vuln)
    body: str | None = None
    query: dict[str, str] = {}
    if wp.values:
        if placement == "body":
            body = _build_body_from_values(wp.values, None)
        else:
            query = dict(wp.values)
    elif wp.raw:
        if placement == "body":
            body = f"{param}={wp.raw}" if param else wp.raw
        elif param:
            query = {param: wp.raw}
    if not method:
        method = "POST" if (placement == "body" or body) else "GET"
    method = method.upper()
    if body and method == "GET":
        method = "POST"
    headers: dict[str, str] = {}
    if body:
        headers["Content-Type"] = _content_type_for_body(body)
    url = _normalize_base_url(base_url) + path
    if query:
        url += "?" + urlencode(query)
    return {"method": method, "url": url, "headers": headers, "body": body}


# --------------------------------------------------------------------------- #
# preconditions 派生
# --------------------------------------------------------------------------- #

# "true"|"false" 是 prompt 契约值；非契约真值（isLoggedIn/user 等）走保留原文分支
_AUTH_TRUE = {"true", "yes", "required", "1"}
_AUTH_FALSE = {"false", "no", "none", "0", "public", "anon", "anonymous", "guest", "optional"}
_NOTES_AUTH_RE = re.compile(r"登录|认证|凭证|会话|login|auth|session|credential", re.IGNORECASE)


def derive_preconditions(vuln: Any) -> str | None:
    """从 authentication_required（"true"|"false" 契约）/notes 派生前置条件；无信号 → None。"""
    ar = _s(getattr(vuln, "authentication_required", None))
    val = (ar or "").strip().lower()
    if val in _AUTH_TRUE:
        return "需登录（携带有效会话凭证）"
    if val in _AUTH_FALSE:
        return "无需登录"
    if val:
        # 非布尔契约值（isLoggedIn / user 等）→ 保守视为需登录并保留原文
        return f"需登录（{ar.strip()}）"
    notes = _s(getattr(vuln, "notes", None))
    if notes and _NOTES_AUTH_RE.search(notes):
        return "需登录（notes 述及认证）"
    return None


def _auth_placeholder_header(vuln: Any) -> dict[str, str]:
    """需登录 → Authorization 占位头（对齐 auth_header 的 Bearer 缺省）。"""
    val = (_s(getattr(vuln, "authentication_required", None)) or "").strip().lower()
    return {"Authorization": "Bearer <AUTH_TOKEN>"} if val in _AUTH_TRUE else {}


# --------------------------------------------------------------------------- #
# expected_response：LLM 产 + 全程降级
# --------------------------------------------------------------------------- #

def build_expected_response_prompt(vuln: Any, request: dict) -> str:
    """expected_response 的 LLM prompt：verdict/mismatch_reason 基础上判成功响应特征。"""
    fields = {k: v for k, v in {
        "vulnerability_type": _s(getattr(vuln, "vulnerability_type", None)),
        "verdict": _s(getattr(vuln, "verdict", None)),
        "mismatch_reason": _s(getattr(vuln, "mismatch_reason", None)),
        "witness": _witness_text(vuln),
        "sink_call": _s(getattr(vuln, "sink_call", None)),
        "sink_function": _s(getattr(vuln, "sink_function", None)),
        "exploitation_hypothesis": _s(getattr(vuln, "exploitation_hypothesis", None)),
        "suggested_exploit_technique": _s(getattr(vuln, "suggested_exploit_technique", None)),
    }.items() if v}
    return (
        "You are defining the success indicator for a vulnerability PoC request.\n\n"
        f"PoC request: {json.dumps(request, ensure_ascii=False)}\n"
        f"Vulnerability facts:\n{json.dumps(fields, ensure_ascii=False, indent=2)}\n\n"
        "Based on the verdict and why the defense fails (mismatch_reason), state what "
        "response characteristic PROVES exploitation succeeded (e.g. SQL error echo, "
        "payload reflected unescaped, internal service response for SSRF).\n"
        'Output JSON {"indicator": "...", "success_criteria": "..."} only.'
    )


def _coerce_expected_response(out: Any) -> dict | None:
    """LLM 产物归一：dict → 校验 indicator；str → JSON（可含 fence）→ 纯文本兜底。"""
    if isinstance(out, dict):
        indicator = out.get("indicator")
        if isinstance(indicator, str) and indicator.strip():
            sc = out.get("success_criteria")
            return {"indicator": indicator.strip(),
                    "success_criteria": sc.strip() if isinstance(sc, str) and sc.strip() else None}
        return None
    if isinstance(out, str):
        s = out.strip()
        if not s:
            return None
        try:
            return _coerce_expected_response(json.loads(s))
        except (json.JSONDecodeError, ValueError):
            m = re.search(r"\{.*\}", s, re.DOTALL)  # 剥 markdown fence 再试
            if m:
                try:
                    return _coerce_expected_response(json.loads(m.group(0)))
                except (json.JSONDecodeError, ValueError):
                    pass
            return {"indicator": s, "success_criteria": None}
    return None


def _llm_expected_response(vuln: Any, request: dict, llm_fn: LlmFn | None) -> dict | None:
    """llm_fn 不给/抛异常/产物不可理解 → None（确定性字段不受影响，绝不抛）。"""
    if llm_fn is None:
        return None
    try:
        return _coerce_expected_response(llm_fn(build_expected_response_prompt(vuln, request)))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# curl / raw_http 确定性生成（从 request dict）
# --------------------------------------------------------------------------- #

def render_curl(poc_dict: dict | None) -> str:
    """request dict → 完整 curl 命令（方法/headers/body 全量，POSIX 引号安全）。"""
    req = (poc_dict or {}).get("request") or {}
    method = str(req.get("method") or "GET").upper()
    url = str(req.get("url") or "")
    parts = [f"curl -i -X {method} {_sh_quote(url)}"]
    for k, v in (req.get("headers") or {}).items():
        parts.append(f"  -H {_sh_quote(f'{k}: {v}')}")
    body = req.get("body")
    if body:
        parts.append(f"  --data {_sh_quote(str(body))}")
    return " \\\n".join(parts)


def _url_parts(url: str) -> tuple[str, str]:
    """url → (netloc, request_target)。手写拆分——urlsplit 对 ``TARGET[:PORT]``
    占位符抛 'Invalid IPv6 URL'（'[' 被当 IPv6 左括号，urlsplit:449）。"""
    rest = url.split("://", 1)[1] if "://" in url else url
    netloc, _, tail = rest.partition("/")
    if not tail:
        return netloc, "/"
    path, q, query = tail.partition("?")
    target = "/" + path
    if q:
        target += "?" + query
    return netloc, target


def render_raw_http(poc_dict: dict | None) -> str:
    """request dict → 原始 HTTP 报文（请求行带 query、Host、headers、body + 长度）。

    对齐 to_burp_raw：Host/Content-Type 唯一产出（入参 headers 里的 Host 跳过、
    Content-Type 缺失时按 body 形态补）、Content-Length 按 utf-8 字节数。
    """
    req = (poc_dict or {}).get("request") or {}
    method = str(req.get("method") or "GET").upper()
    netloc, target = _url_parts(str(req.get("url") or ""))
    headers = {str(k): str(v) for k, v in (req.get("headers") or {}).items()}
    body = req.get("body")
    body_text = str(body) if body else None
    lines = [f"{method} {target} HTTP/1.1"]
    if netloc:
        lines.append(f"Host: {netloc}")
    for k, v in headers.items():
        if k.lower() == "host":
            continue
        lines.append(f"{k}: {v}")
    if body_text is not None:
        if not any(k.lower() == "content-type" for k in headers):
            lines.append(f"Content-Type: {_content_type_for_body(body_text)}")
        lines.append(f"Content-Length: {len(body_text.encode('utf-8'))}")
    lines.append("")
    if body_text is not None:
        lines.append(body_text)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 组装 + 写回
# --------------------------------------------------------------------------- #

def build_structured_poc(vuln: Any, base_url: str | None,
                         llm_fn: LlmFn | None = None) -> dict | None:
    """单漏洞 → PocBlock 形态 dict（见模块 docstring 分层）。

    Returns:
        dict：request（确定性）+ preconditions + expected_response（LLM，可缺省
        None）+ witness_payload + curl/raw_http（request 确定性派生）。
        None：vuln 为空或提不出路由锚点（无 path 可提且 witness 无请求行）——
        调用方回退现行确定性模板路径（spec §5.6 降级矩阵 ③）。
    """
    if vuln is None:
        return None
    request = _build_request(vuln, base_url)
    if request is None:
        return None
    headers = dict(request["headers"])
    headers.update(_auth_placeholder_header(vuln))
    request = {**request, "headers": headers}
    poc = {
        "request": request,
        "preconditions": derive_preconditions(vuln),
        "expected_response": _llm_expected_response(vuln, request, llm_fn),
        "witness_payload": _witness_text(vuln),
        "curl": render_curl({"request": request}),
        "raw_http": render_raw_http({"request": request}),
    }
    return poc


def apply_structured_poc(queue_entry: dict, poc_dict: dict | None) -> dict:
    """把 poc dict 写入 queue entry 的 ``report_poc`` 字段（原地，返回 entry）。

    poc_dict=None 显式清空（上层判定无 POC 时不留旧值）。
    """
    queue_entry["report_poc"] = poc_dict
    return queue_entry
