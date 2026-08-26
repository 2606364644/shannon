"""黑盒 report_data 组装器（spec 2026-08-26-report-generation-agent-design §6.1 / T7）。

数据源 = ``deliverables/blackbox/{vc}_exploit_verdicts.json``（exploit agent 的
结构化判定，5 档 verdict schema 见 ``collectors/exploit.py``）→ 确定性映射
``ReportData``（§4 同 schema）。黑盒数据已富（实测证据在 verdicts 里），本组装器
只做结构化，无 LLM 步骤：

- ``evidence.verification="dynamic"``、``dynamic_evidence``=实测输出
  （exploited→proof_of_impact；blocked/potential→evidence_of_vulnerability）；
- ``poc.request``=实际发出的请求（从 exploitation_steps/what_we_tried 里的 curl
  命令确定性解析；散文 ``METHOD http://...`` 形态兜底；完全提不出 → None 不编造），
  ``expected_response``=实测观察，curl/raw_http 由 request 确定性生成；
- stats 聚合（by_type/by_severity）；``raw`` 保留原始 verdict entry；
- executive_summary/qa 留 None（T5 执行摘要 agent 后续接入）。

与白盒组装器（``report_data_builder.py``，T1 并行编写）互不依赖；落盘函数
``write_report_data`` 优先复用其同名实现，import 失败（尚未落地）则用本模块
兜底版（同语义：原子写、ensure_ascii=False）。
"""
from __future__ import annotations

import inspect
import json
import logging
import re
import shlex
from pathlib import Path
from urllib.parse import urlparse

from supernova_core.models.report_data import (
    EndpointEntry,
    PocBlock,
    PocExpectedResponse,
    PocRequest,
    ReportData,
    ReportStats,
    ReportVulnerability,
    ScanMeta,
    TypeStats,
    VulnEvidence,
    VulnNarrative,
)
from supernova_core.utils.atomic_write import atomic_write_json
from supernova_core.utils.file_io import async_read_file, async_path_exists
from supernova_core.utils.paths import BLACKBOX_SUBDIR, resolve_track_deliverable

logger = logging.getLogger(__name__)

# verdict 严重度排序（worst→best）；severity_range 按此序取跨度。
_SEVERITY_RANK: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# 步骤自带的编号前缀（对齐 renderers/exploit._strip_step_num）。
_STEP_NUM_RE = re.compile(r"^\s*(?:\d+[.、)]\s*|步骤\s*\d+\s*[-—:.：]\s*)")

# curl 命令定位：规避 URL/路径内部引用（`/curl`、`curl.example.com`）。
_CURL_RE = re.compile(r"(?<![\w/.@-])curl\b")

# 散文请求形态兜底：`POST http://target/path`（无 curl 结构时尽力提 method+url）。
_PROSE_REQUEST_RE = re.compile(
    r"(?<![\w-])(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(https?://[^\s'\"<>]+)")

# 前置条件关键词（登录/会话类步骤 → poc.preconditions）。
_PRECONDITION_RE = re.compile(
    r"(?i)(login|log\s?in|authenticate|sign\s?in|cookie|session|登录|登入|认证|憑證|凭证)")

# curl 数据类 flag → (值个数, 归类)。归类：body=原始 -d 值；urlencode=--data-urlencode 值。
_CURL_DATA_FLAGS = {
    "-d": "body", "--data": "body", "--data-raw": "body",
    "--data-binary": "body", "--data-ascii": "body",
    "--data-urlencode": "urlencode",
}


def _strip_step_num(s: str) -> str:
    return _STEP_NUM_RE.sub("", s, count=1)


# ── verdict → ReportVulnerability ─────────────────────────────────────────────

def _verdict_texts(verdict: dict) -> list[str]:
    """按序收集可能含「实际发出的请求」的文本（步骤优先，其次尝试/证据）。"""
    texts: list[str] = []
    steps = verdict.get("exploitation_steps")
    if isinstance(steps, list):
        texts.extend(str(s) for s in steps if s)
    for key in ("what_we_tried", "proof_of_impact", "evidence_of_vulnerability"):
        val = verdict.get(key)
        if val:
            texts.append(str(val))
    return texts


def _parse_curl_command(candidate: str) -> "PocRequest | None":
    """单段 curl 命令文本 → PocRequest；提不出 URL 返回 None（不编造）。"""
    try:
        tokens = shlex.split(candidate)
    except ValueError:
        return None  # 引号不闭合（截断/散文）→ 放弃该候选
    if not tokens or tokens[0] != "curl":
        return None
    method: str | None = None
    url: str | None = None
    headers: dict[str, str] = {}
    body_parts: list[str] = []
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if tok in ("-X", "--request") and nxt:
            method = nxt.upper()
            i += 2
        elif tok in ("-H", "--header") and nxt:
            k, _, v = nxt.partition(":")
            if k.strip():
                headers[k.strip()] = v.strip()
            i += 2
        elif tok in ("-b", "--cookie") and nxt:
            headers.setdefault("Cookie", nxt)
            i += 2
        elif tok in _CURL_DATA_FLAGS and nxt:
            body_parts.append(nxt)
            i += 2
        else:
            if tok.startswith(("http://", "https://")) and url is None:
                url = tok
            i += 1
    if url is None:
        return None
    if method is None:
        method = "POST" if body_parts else "GET"
    body: str | None = None
    if body_parts:
        # 多个 -d/--data-urlencode 按出现序 & 拼接（curl 语义）。
        body = "&".join(body_parts)
    return PocRequest(method=method, url=url, headers=headers, body=body)


def _extract_request(texts: list[str]) -> "PocRequest | None":
    """两级确定性提取：① curl 命令（method/url/headers/body 全）② 散文 METHOD+URL。"""
    for text in texts:
        m = _CURL_RE.search(text)
        while m is not None:
            req = _parse_curl_command(text[m.start():])
            if req is not None:
                return req
            m = _CURL_RE.search(text, m.start() + 1)
    for text in texts:
        m = _PROSE_REQUEST_RE.search(text)
        if m is not None:
            return PocRequest(method=m.group(1).upper(), url=m.group(2))
    return None


def _extract_preconditions(texts: list[str]) -> "str | None":
    """首个含认证/会话关键词且不含 curl 命令的步骤 → preconditions。"""
    for text in texts:
        if _CURL_RE.search(text):
            continue
        if _PRECONDITION_RE.search(text):
            return _strip_step_num(text).strip()
    return None


def _render_curl(req: PocRequest) -> str:
    parts = [f"curl -X {req.method} {shlex.quote(req.url)}"]
    for k, v in req.headers.items():
        parts.append(f"-H {shlex.quote(f'{k}: {v}')}")
    if req.body is not None:
        parts.append(f"--data {shlex.quote(req.body)}")
    return " ".join(parts)


def _render_raw_http(req: PocRequest) -> str:
    p = urlparse(req.url)
    target = p.path or "/"
    if p.query:
        target = f"{target}?{p.query}"
    lines = [f"{req.method} {target} HTTP/1.1", f"Host: {p.netloc}"]
    for k, v in req.headers.items():
        lines.append(f"{k}: {v}")
    if req.body is not None:
        lines.extend(["", req.body])
    return "\n".join(lines)


def _map_verdict(vc: str, verdict: dict) -> ReportVulnerability:
    status = str(verdict.get("status") or "")
    exploited = status == "exploited"

    # evidence：dynamic + 实测输出（按 status 取对应证据字段）。
    dynamic_evidence = (
        verdict.get("proof_of_impact") if exploited else
        verdict.get("evidence_of_vulnerability") or verdict.get("evidence")
    )
    notes: str | None = None
    if status == "blocked_by_security":
        blocker = verdict.get("current_blocker")
        tried = verdict.get("what_we_tried")
        notes = "; ".join(x for x in (blocker, tried) if x) or None
    elif status == "potential":
        notes = verdict.get("downgrade_reason")
    evidence = VulnEvidence(
        verification="dynamic",
        dynamic_evidence=str(dynamic_evidence) if dynamic_evidence else None,
        verdict=status or None,
        notes=notes,
    )

    # poc：实际发出的请求 + 实测观察。
    texts = _verdict_texts(verdict)
    request = _extract_request(texts)
    expected = (
        PocExpectedResponse(indicator=str(dynamic_evidence))
        if dynamic_evidence else None
    )
    poc = None
    if request is not None or expected is not None:
        poc = PocBlock(
            request=request,
            preconditions=_extract_preconditions(texts),
            expected_response=expected,
            curl=_render_curl(request) if request is not None else None,
            raw_http=_render_raw_http(request) if request is not None else None,
        )

    # endpoints：从实测请求确定性派生（method+path；行号/参数黑盒无 → 缺省）。
    endpoints: list[EndpointEntry] = []
    if request is not None:
        path = urlparse(request.url).path or "/"
        endpoints.append(EndpointEntry(method=request.method, path=path))

    narrative = VulnNarrative(
        impact=(
            verdict.get("impact") if exploited
            else verdict.get("expected_impact")
        ),
    )
    return ReportVulnerability(
        id=str(verdict.get("vulnerability_id") or f"{vc}-UNKNOWN"),
        type=vc,
        severity=verdict.get("severity"),
        # exploited=实测复现成功 → 确定性映射 high；其余档直传 verdict confidence。
        confidence="high" if exploited else verdict.get("confidence"),
        cvss=verdict.get("cvss"),
        cwe_id=verdict.get("cwe_id"),
        owasp_category=verdict.get("owasp_category"),
        narrative=narrative if narrative.impact else None,
        endpoints=endpoints,
        poc=poc,
        evidence=evidence,
        raw=verdict,
    )


# ── stats 聚合 ────────────────────────────────────────────────────────────────

def _aggregate_stats(vulns: list[ReportVulnerability]) -> ReportStats:
    by_type: dict[str, TypeStats] = {}
    by_severity: dict[str, int] = {}
    for v in vulns:
        ts = by_type.setdefault(v.type, TypeStats())
        ts.count += 1
        if v.severity:
            by_severity[v.severity] = by_severity.get(v.severity, 0) + 1
    for vc, ts in by_type.items():
        severities = sorted(
            {v.severity for v in vulns
             if v.type == vc and v.severity and v.severity in _SEVERITY_RANK},
            key=lambda s: _SEVERITY_RANK[s],
        )
        if severities:
            ts.severity_range = severities[0] if len(severities) == 1 \
                else f"{severities[0]}-{severities[-1]}"
    return ReportStats(by_type=by_type, by_severity=by_severity)


# ── 组装入口 ──────────────────────────────────────────────────────────────────

async def build_blackbox_report_data(
    deliverables_path: Path | str, scan_meta: ScanMeta,
) -> ReportData:
    """verdicts → ReportData（确定性，无 LLM）。

    逐 vuln class 读 ``{vc}_exploit_verdicts.json``（resolve_track_deliverable
    三级链：blackbox/intermediate/（现行）→ blackbox/ 顶层（legacy）→ 根平铺）。
    文件缺失=该类不在范围（跳过）；损坏=warning 跳过该类（non-fatal，其余照常）。
    verdicts 里的全部 accepted 状态（exploited/blocked/potential/other）都进卡，
    状态落 ``evidence.verdict``；rejected 不进卡（对齐 evidence md 口径）。
    """
    from supernova_core.models.agents import ALL_VULN_CLASSES

    deliverables = Path(deliverables_path)
    vulns: list[ReportVulnerability] = []
    for vc in ALL_VULN_CLASSES:
        path = resolve_track_deliverable(
            deliverables, BLACKBOX_SUBDIR, f"{vc}_exploit_verdicts.json")
        if not await async_path_exists(path):
            continue
        try:
            payload = json_loads_or_none(await async_read_file(path))
        except Exception as exc:  # noqa: BLE001 — 单类损坏不拖垮整报告
            logger.warning("blackbox %s verdicts read failed (skip class): %s",
                           vc, exc)
            continue
        if payload is None:
            logger.warning("blackbox %s verdicts unparseable (skip class)", vc)
            continue
        for verdict in payload.get("verdicts") or []:
            if isinstance(verdict, dict):
                vulns.append(_map_verdict(vc, verdict))
    return ReportData(
        scan=scan_meta,
        stats=_aggregate_stats(vulns),
        vulnerabilities=vulns,
    )


def json_loads_or_none(text: str) -> "dict | None":
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ── scan meta（session.json → ScanMeta）───────────────────────────────────────

def build_scan_meta_from_session(
    session_data: dict | None, fallback_id: str,
) -> ScanMeta:
    """黑盒 ScanMeta（best-effort）：session.json 有则取 id/date/duration/cost/model。

    session.json 由 MetricsTracker 全程增量落盘（web 路径）；缺失/字段缺省时
    回落 fallback_id / None——meta 不全不阻塞报告产出。
    """
    data = session_data or {}
    session = data.get("session") or {}
    metrics = data.get("metrics") or {}
    agents = metrics.get("agents") or {}
    models = sorted({
        str(a.get("model"))
        for a in agents.values()
        if isinstance(a, dict) and a.get("model")
    })
    return ScanMeta(
        id=str(session.get("id") or fallback_id),
        track="blackbox",
        date=session.get("createdAt"),
        duration_ms=metrics.get("total_duration_ms"),
        cost=metrics.get("total_cost_usd"),
        currency=metrics.get("cost_currency"),
        model=", ".join(models) or None,
    )


# ── 落盘（复用 T1 白盒同名函数；缺则兜底）────────────────────────────────────

try:  # T1 report_data_builder 落地后自动复用（签名兼容：(rd, path)）
    from supernova_core.services.report_data_builder import (  # type: ignore
        write_report_data,
    )
except Exception:  # pragma: no cover — T1 未落地期 / import 半成品兜底

    async def write_report_data(report_data: ReportData, path: Path | str) -> None:
        """ReportData → report_data.json（原子写，ensure_ascii=False）。"""
        atomic_write_json(Path(path), report_data.model_dump())


async def write_blackbox_report_data(
    deliverables_path: Path | str,
    session_path: Path | str | None,
    fallback_id: str,
) -> Path:
    """管线接线一步到位：读 session.json（best-effort）→ 组装 → 落 blackbox/report_data.json。

    返回输出路径（activity 记日志用）。失败上抛由调用方按 non-fatal 包裹。
    """
    session_data = None
    if session_path and await async_path_exists(Path(session_path)):
        session_data = json_loads_or_none(
            await async_read_file(Path(session_path)))
    scan_meta = build_scan_meta_from_session(session_data, fallback_id)
    report_data = await build_blackbox_report_data(deliverables_path, scan_meta)
    bb = Path(deliverables_path) / BLACKBOX_SUBDIR
    out = bb / "report_data.json"
    # T1 同名函数并发编写期签名可能漂移（sync/async）——awaitable 探测双兼容。
    result = write_report_data(report_data, out)
    if inspect.isawaitable(result):
        await result
    return out
