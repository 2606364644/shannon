# packages/core/src/supernova_core/services/poc_generator.py
"""外部可达漏洞 PoC 自动生成（curl / Burp），报告后处理。"""
from __future__ import annotations

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

_GN_SOURCE_RE = re.compile(
    r"^(\S+)\s*\((.+?):([^/:]+):(\d+)\)\s*$"
)


def extract_gn_location(source: str | None) -> tuple[str | None, str | None, str | None]:
    """从 GitNexus 轨 source 提取 (param_name, file_path, method)。

    GitNexus builder 的 _source_text 产 'param (file:method:line)' 形态
    （如 'payload (…/Controller.java:apiModifyClusterConfig:70)'）。
    file 可含 '/'/'.'；method 是单个标识符（不含 ':/'）；line 是纯数字。
    非 GitNexus 格式（LLM 轨的 '@RequestBody at Foo.java:71' 等）→ (None, None, None)。
    """
    if not source:
        return (None, None, None)
    m = _GN_SOURCE_RE.match(source.strip())
    if not m:
        return (None, None, None)
    return (m.group(1), m.group(2), m.group(3))


@dataclass
class PartialSpec:
    """确定性提取的部分 PoC spec（inj/xss/ssrf 分层组装中间结构）。

    route/witness 任一缺失 → needs_gap_fill=True，归入按 controller 文件分组的 LLM 补缺。
    """
    vuln: Any
    vuln_class: str
    band: ConfidenceBand
    param_name: str | None
    placement: str            # "query" | "body"
    controller_file: str | None
    method: str | None
    path: str | None
    witness: str | None

    @property
    def needs_gap_fill(self) -> bool:
        return not self.method or not self.path or not self.witness


def _extract_deterministic(
    vuln: Any, vuln_class: str, endpoints: dict, band: ConfidenceBand
) -> PartialSpec:
    """从 vuln 确定性提取 PartialSpec（不调 LLM）。缺 route/witness 时 needs_gap_fill=True。"""
    method, path = derive_method_path(vuln)
    param = extract_param_name(getattr(vuln, "source", None))
    gn_param, gn_file, _gn_method = extract_gn_location(getattr(vuln, "source", None))
    if not param and gn_param:
        param = gn_param
    witness = getattr(vuln, "witness_payload", None) or None
    placement = "body" if vuln_class == "ssrf" else "query"
    return PartialSpec(
        vuln=vuln, vuln_class=vuln_class, band=band, param_name=param,
        placement=placement, controller_file=gn_file,
        method=method, path=path, witness=witness,
    )


def _assemble(partial: PartialSpec, gap: dict | None, endpoints: dict) -> HttpRequestSpec:
    """用确定性 partial + LLM gap-fill({http_method,route_path,witness_payload}) 组装最终 spec。

    route 补回后重查 recon endpoints 得 auth_state。无 gap/缺 witness → 骨架 + 标注。
    """
    g = gap or {}
    method = partial.method or (g.get("http_method") or "GET")
    path = partial.path or (g.get("route_path") or "/")
    witness = partial.witness or g.get("witness_payload") or ""
    info = find_endpoint_info(endpoints, path)
    auth_st = derive_auth_state(info)
    spec = HttpRequestSpec(
        method=str(method).upper(), path=path,
        headers=auth_header(auth_st, info), auth_state=auth_st,
        confidence_band=partial.band,
        source_id=getattr(partial.vuln, "ID", ""), vuln_class=partial.vuln_class,
    )
    if not witness:
        spec.note = "请求形态未推断（缺 witness），需手工补全 body/参数"
        return spec
    # 按 vuln_class 决定参数位（对齐既有 build_template_spec 逻辑）
    if partial.vuln_class == "ssrf":
        if _is_open_redirect(partial.vuln):
            param = partial.param_name or "next"
            spec.query = {param: witness}
        else:
            param = getattr(partial.vuln, "vulnerable_parameter", None) or partial.param_name or "url"
            if spec.method == "GET":
                spec.method = "POST"
            spec.body = f"{param}={witness}"
    else:  # injection / xss
        param = partial.param_name or ("id" if partial.vuln_class == "injection" else "q")
        spec.query = {param: witness}
    return spec


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
    items_desc = json.dumps([
        {"ID": getattr(p.vuln, "ID", ""), "param": p.param_name,
         "method_hint": None, "vuln_class": p.vuln_class,
         "evidence_chain": (getattr(p.vuln, "evidence_chain", None) or "")[:300]}
        for p in partials
    ], ensure_ascii=False)
    file_line = f"Handler file: {file_key}\n" if file_key else "Handler file: unknown\n"
    return (
        f"You are reconstructing HTTP request shapes for confirmed vulnerabilities.\n\n"
        f"{file_line}Read that file and find each handler method's HTTP route "
        f"(@PostMapping / router.get / @app.route …) and a minimal witness payload.\n\n"
        f"Vulnerabilities to fill:\n{items_desc}\n\n"
        f"Recon endpoint context:\n{json.dumps(recon_ctx, ensure_ascii=False)}\n\n"
        f"Output JSON {{\"items\":[{{\"ID\",\"http_method\",\"route_path\","
        f"\"witness_payload\"}}]}}. Output JSON only."
    )


async def llm_fill_gaps(
    file_key: str | None, partials: list["PartialSpec"], *, recon_ctx: dict,
    repo_path: str, api_key: str | None = None, model_tier: str = "medium",
) -> dict[str, dict]:
    """一个 controller 文件组一次 LLM 调用,返回 {ID: {http_method,route_path,witness_payload}}。

    失败/不可用 → 返回 {}(调用方对缺 gap 的条目降级骨架)。
    """
    prompt = _build_gapfill_prompt(file_key, partials, recon_ctx)
    try:
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=repo_path or "/tmp/poc-gen",
            model_tier=model_tier,
            # runner 现状:output_format 是主参(structured_output_schema 为别名,见 runner.py:139)。
            # chain_verdict 的 _make_verdict_llm_client 也走 output_format,此处对齐。
            output_format=GAPFILL_OUTPUT_SCHEMA,
            api_key=api_key,
            max_turns=int(os.getenv("SUPERNOVA_POC_MAX_TURNS", "10")),
        )
    except Exception:
        return {}
    if not getattr(result, "success", False) or not getattr(result, "structured_output", None):
        return {}
    items = result.structured_output.get("items") or []
    out: dict[str, dict] = {}
    for it in items:
        vid = it.get("ID")
        if vid:
            out[vid] = {
                "http_method": it.get("http_method"),
                "route_path": it.get("route_path"),
                "witness_payload": it.get("witness_payload"),
            }
    return out


async def _batch_fill_gaps(
    partials: list["PartialSpec"], *, endpoints: dict, repo_path: str,
    api_key: str | None = None, model_tier: str = "medium",
) -> dict[str, dict]:
    """编排:分组 + 逐组调 llm_fill_gaps,合并 {ID: gap}。失败的组其条目无 gap(后降级)。"""
    cap = int(os.getenv("SUPERNOVA_POC_GROUP_CAP", "8"))
    groups = _group_by_controller_file(partials, cap=cap)
    gapmap: dict[str, dict] = {}
    for file_key, group_partials in groups:
        recon_ctx = {ep: info for ep, info in endpoints.items()} if endpoints else {}
        try:
            gapmap.update(await llm_fill_gaps(
                file_key, group_partials, recon_ctx=recon_ctx,
                repo_path=repo_path, api_key=api_key, model_tier=model_tier))
        except Exception as exc:  # 单组失败不阻塞其余
            logger.warning("poc: llm_fill_gaps failed for %s: %s", file_key, exc)
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
            detail.append(_detail_section(vuln_class, vuln, s, host).replace(
                f" · {vuln_class} @ ", f"{heading} · {vuln_class} @ "))
            detail.append("\n---\n")
    return header + "\n".join(overview) + "\n" + "\n".join(detail)


def empty_poc_md(track: str) -> str:
    """空表兜底：无 externally_exploitable 漏洞时调用。"""
    track_cn = "白盒" if track == "whitebox" else "黑盒"
    return f"# 可利用漏洞 PoC 集合（{track_cn}）\n\n本次扫描无 externally_exploitable 漏洞，未生成 PoC。\n"


# Task 7: generate() 主流程（编排 + 过滤 + LLM 仲裁 + 降级 + 读写）

logger = logging.getLogger(__name__)
_POC_FILENAME = "exploitable_poc_collection.md"
_POC_CHECKPOINT_FILENAME = ".poc_checkpoint.json"


def _ckpt_path(deliverables_dir: Path) -> Path:
    return deliverables_dir / _POC_CHECKPOINT_FILENAME


def _load_checkpoint(deliverables_dir: Path) -> dict:
    """读 sidecar checkpoint。损坏/缺失 → 返回空(从头跑,降级不报错)。"""
    p = _ckpt_path(deliverables_dir)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("completed", {}) if isinstance(data, dict) else {}
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
        tmp.write_text(json.dumps(
            {"version": 1, "track": track, "completed": completed}, ensure_ascii=False),
            encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        logger.warning("poc: checkpoint write failed (non-blocking)")


def _resolve_input(deliverables_dir: Path, filename: str) -> Path | None:
    """先在 track 目录找，不存在回退 parent（兼容老平铺 session）。"""
    p = deliverables_dir / filename
    if p.exists():
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
                if not getattr(v, "externally_exploitable", False):
                    continue
                items.append((vc, v, accepted))

        total = len(items)
        track_cn = "白盒" if track == "whitebox" else "黑盒"
        await _poc_progress(f"{track_cn} PoC: {total} 个 externally_exploitable 漏洞")

        entries: list[tuple[str, Any, HttpRequestSpec | list[HttpRequestSpec]]] = []
        entries_by_idx: dict[int, tuple[str, Any, HttpRequestSpec | list[HttpRequestSpec]]] = {}
        # inj/xss/ssrf 的待补项(模板未命中),收集后按文件分组批量补缺
        gapped: list[tuple[int, "PartialSpec"]] = []
        # Fix B:断点续传 — 读 checkpoint,reuse 已完成项,retry 不从零重来
        ckpt_completed = _load_checkpoint(deliverables_dir)
        ckpt_done_ids = set(ckpt_completed.keys())

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
                    # authz(成对模板)/auth(量小,上游 §5.3 默认 LLM)保持既有 per-item 路径
                    spec = await PoCGenerator._build_entry(
                        v, vc, host, endpoints, accepted,
                        repo_path=repo_path, api_key=api_key, model_tier=model_tier)
                    dt_ms = int((time.monotonic() - t0) * 1000)
                    if spec is not None:
                        entries_by_idx[i] = (vc, v, spec)
                        await _poc_progress(f"{label}  {format_duration(dt_ms)}")
                    else:
                        await _poc_progress(f"{label}  skip {format_duration(dt_ms)}")
                else:
                    # inj/xss/ssrf:模板优先(0ms);未命中 → 收集待补
                    band = classify_confidence(v, is_accepted=(vid in accepted))
                    template = build_template_spec(v, vc, host, endpoints, band)
                    if template is not None:
                        entries_by_idx[i] = (vc, v, template)
                        await _poc_progress(f"{label}  {format_duration(int((time.monotonic()-t0)*1000))}")
                    else:
                        partial = _extract_deterministic(v, vc, endpoints, band)
                        gapped.append((i, partial))
                        await _poc_progress(f"{label}  待补缺(分组) {format_duration(int((time.monotonic()-t0)*1000))}")
                # Fix B:增量写 checkpoint(模板/authz/auth 路径;gapped 待补项此处尚未 resolve,在分组补缺后统一写)
                if i in entries_by_idx:
                    _vc, _v, _spec = entries_by_idx[i]
                    ckpt_completed[getattr(_v, "ID", str(i))] = {
                        "vuln_class": _vc, "spec": _spec_to_ckpt(_spec)}
                    _write_checkpoint(deliverables_dir, track, ckpt_completed)
            except Exception as exc:  # 单条失败不阻塞其余
                dt_ms = int((time.monotonic() - t0) * 1000)
                logger.warning("poc: build failed for %s: %s", vid, exc)
                await _poc_progress(f"{label}  — {exc} ({format_duration(dt_ms)})")

        # 分组批量补缺(GitNexus 轨缺 route/witness 的项)
        if gapped:
            await _poc_progress(f"PoC 分组补缺: {len(gapped)} 条待补")
            gapmap = await _batch_fill_gaps(
                [p for _, p in gapped], endpoints=endpoints,
                repo_path=repo_path or "/tmp/poc-gen", api_key=api_key, model_tier=model_tier)
            for i, partial in gapped:
                vid = getattr(partial.vuln, "ID", "?")
                spec = _assemble(partial, gapmap.get(vid), endpoints)
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
                           repo_path, api_key, model_tier) -> HttpRequestSpec | list[HttpRequestSpec] | None:
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
                                   api_key=api_key, model_tier=model_tier)
        if not guess:
            # LLM 不可用/失败 → 骨架 + 标注
            spec = _base_spec(vuln, vuln_class, endpoints, band)
            spec.note = "请求形态未推断（LLM 不可用），需手工补全 body/参数"
            return spec
        if guess.get("steps"):
            return [_spec_from_llm_guess(s, vuln, vuln_class, band) for s in guess["steps"]]
        return _spec_from_llm_guess(guess, vuln, vuln_class, band)
