"""白盒 report_data 组装器（spec 2026-08-26-report-generation-agent-design §3/§4）。

确定性步骤：读合并后 SSOT（``{vc}_exploitation_queue.json``）+ attack_chains，
映射为 ``models/report_data.ReportData``。agent 富化（①归并终审 merged_from /
②接口表 report_endpoints / ③POC report_poc）已写回 queue 的结构化字段在此
优先采用；agent 步骤未跑/失败时现有确定性字段照常组装——报告永远完整。

纯数据搬运无渲染：md 导出（report_markdown_exporter）与前端渲染都吃本产物。
"""
import json
import logging
import re

from supernova_core.models.report_data import (
    AttackChainEntry,
    EndpointEntry,
    PocBlock,
    QuickReferenceRow,
    ReportData,
    ReportStats,
    ReportVulnerability,
    ScanMeta,
    TypeStats,
    VulnEvidence,
    VulnNarrative,
    ProblemPoint,
)
from supernova_core.models.queue_schemas import VulnerabilityQueue
from supernova_core.services.findings_renderer import CLASS_CONFIG
# 速查表行口径复用 report_assembler 单元格函数（勿抄逻辑——与 md 现速查表
# 同源，spec 2026-08-26-report-single-source-rendering §5「渲染层纯渲染」）
from supernova_core.services.report_assembler import (
    _confidence_cell,
    _endpoint_cell,
    _report_endpoint_params,
    _report_endpoint_routes,
    _severity_cell,
    _type_title,
    _verification_cell,
)
from supernova_core.services.severity_rules import (
    SEVERITY_ORDER,
    effective_severity,
)
from supernova_core.utils.file_io import async_path_exists, async_read_file, async_write_file
from supernova_core.utils.paths import resolve_intermediate

logger = logging.getLogger(__name__)

# queue entry verification 字段值 → report_data evidence.verification 枚举
_VERIFICATION_MAP = {"static_analysis": "static", "dynamically_verified": "dynamic"}

# endpoints 串元素契约 "METHOD /path (role, auth)?"——role 已知集合外的单段
# 括号内容视为 auth（"GET /memos (trigger)" vs "POST /x (isLoggedIn)"）。
_ENDPOINT_RE = re.compile(
    r"^\s*(?:(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+)?([^\s(]+)\s*(?:\((.*?)\))?\s*$",
    re.IGNORECASE,
)
_KNOWN_ROLES = {"write", "trigger", "read", "update", "delete"}


def parse_endpoint_string(raw: str) -> EndpointEntry | None:
    """端点串 → EndpointEntry（确定性；解析不出 path 返回 None）。

    支持形态：``POST /memos (write, isLoggedIn)``、``GET /memos (trigger)``、
    ``POST /login``、``/allocations/:userId``（无 method）。
    """
    if not raw or not isinstance(raw, str):
        return None
    m = _ENDPOINT_RE.match(raw.strip())
    if not m:
        return None
    method, path, paren = m.group(1), m.group(2), m.group(3)
    if not path.startswith("/"):
        # 无方法前缀且不以 / 开头（如纯参数名）——不是端点
        if method is None:
            return None
    entry = EndpointEntry(
        method=method.upper() if method else None, path=path)
    if paren:
        parts = [p.strip() for p in paren.split(",") if p.strip()]
        for i, part in enumerate(parts):
            if part.lower() in _KNOWN_ROLES and entry.role is None:
                entry.role = part.lower()
            elif entry.auth is None:
                entry.auth = part
    return entry


def _backfill_deterministic_entries(vuln, entries: list[EndpointEntry]) -> None:
    """确定性路径数据补齐（spec 单源化 §5）：params ← affected_parameters
    （去重保序，卡级集合对串解析/单端兜底产出的每个 entry 共享——多接口卡
    无 per-endpoint 归属数据，不虚构归属）；行号链（sink/source/route）←
    affected_entries 首个非空值兜底。富化路径（report_endpoints）不进此函数。"""
    params: list[str] = []
    for p in (getattr(vuln, "affected_parameters", None) or []):
        p = str(p).strip()
        if p and p not in params:
            params.append(p)
    entries_meta = [e for e in (getattr(vuln, "affected_entries", None) or [])
                    if isinstance(e, dict)]

    def first_non_empty(key: str) -> str | None:
        for e in entries_meta:
            v = e.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    sink_loc = first_non_empty("sink_location")
    src_loc = first_non_empty("source_location")
    route_at = first_non_empty("route_registered_at")
    for entry in entries:
        if params:
            entry.params = list(params)
        if sink_loc:
            entry.sink_location = sink_loc
        if src_loc:
            entry.source_location = src_loc
        if route_at:
            entry.route_registered_at = route_at


def _endpoint_entries(vuln) -> list[EndpointEntry]:
    """接口一体表（确定性派生）：report_endpoints（②富化写回）优先 →
    endpoints 串解析 → endpoint/path 单端兜底（后两者走 §5 回填）。"""
    structured = getattr(vuln, "report_endpoints", None)
    if structured:
        entries = []
        for item in structured:
            try:
                entries.append(EndpointEntry.model_validate(item))
            except Exception:  # noqa: BLE001 — 富化产物畸形逐条丢弃
                logger.warning("report_endpoints 畸形条目丢弃: %r", item)
        if entries:
            return entries
    entries: list[EndpointEntry] = []
    for raw in (getattr(vuln, "endpoints", None) or []):
        parsed = parse_endpoint_string(str(raw))
        if parsed is not None:
            entries.append(parsed)
    if not entries:
        # 兜底：endpoint（归一化串）或 path 提取
        fallback_src = getattr(vuln, "endpoint", None) or getattr(vuln, "path", None)
        if fallback_src:
            from supernova_core.code_index.gn_collapse import extract_endpoint
            extracted = extract_endpoint(fallback_src)
            if extracted:
                parsed = parse_endpoint_string(extracted)
                if parsed is not None:
                    entries = [parsed]
    if entries:
        _backfill_deterministic_entries(vuln, entries)
    return entries


def _poc_block(vuln) -> PocBlock | None:
    """POC（确定性部分）：report_poc（③富化写回）优先 → witness_payload 族。"""
    structured = getattr(vuln, "report_poc", None)
    if isinstance(structured, dict):
        try:
            return PocBlock.model_validate(structured)
        except Exception:  # noqa: BLE001
            logger.warning("report_poc 畸形，回退 witness_payload")
    witness = (getattr(vuln, "witness_payload", None)
               or getattr(vuln, "minimal_witness", None))
    if witness:
        return PocBlock(witness_payload=str(witness))
    return None


def _narrative(vuln) -> VulnNarrative | None:
    cause = getattr(vuln, "notes", None)
    impact = getattr(vuln, "impact", None)
    remediation = getattr(vuln, "remediation", None)
    if cause or impact or remediation:
        return VulnNarrative(
            cause=cause, impact=impact, remediation=remediation)


def _problem_points(vuln, vuln_class: str) -> list[ProblemPoint]:
    """report_problem_points（富化写回）→ ProblemPoint 透传（不合成不推断）。

    宽松校验与 endpoint 写回同立场：非 dict / 缺 location 的畸形条目丢弃。
    auth/authz 卡不走 endpoint_enrichment（spec 单源化 §5）——写回空时确定性
    兜底：location ← findings_renderer 位置回退链（_card_loc/_card_sink，与
    md 卡问题点节同源），snippet ← queue code_snippet 透传，description 无。
    taint 卡不兜底（富化 agent 职责）。
    """
    points: list[ProblemPoint] = []
    for item in (getattr(vuln, "report_problem_points", None) or []):
        if not isinstance(item, dict):
            continue
        location = str(item.get("location") or "").strip()
        if not location:
            continue
        points.append(ProblemPoint(
            location=location,
            description=item.get("description"),
            snippet=item.get("snippet"),
        ))
    if points or vuln_class not in ("auth", "authz"):
        return points
    # auth/authz 确定性兜底：位置链复用 findings_renderer 私有 helper
    from supernova_core.services.findings_renderer import _card_loc, _card_sink
    _, sink_loc = _card_sink(vuln)
    loc = _card_loc(vuln, sink_loc)
    if not loc:
        return []
    snippet = getattr(vuln, "code_snippet", None)
    return [ProblemPoint(
        location=loc,
        snippet=snippet if isinstance(snippet, str) and snippet.strip() else None,
    )]


def _evidence(vuln) -> VulnEvidence:
    raw_verif = (getattr(vuln, "verification", None) or "").strip().lower()
    return VulnEvidence(
        verification=_VERIFICATION_MAP.get(raw_verif, "static"),
        verdict=getattr(vuln, "verdict", None),
        code_snippet=getattr(vuln, "code_snippet", None),
    )


def _report_vulnerability(vuln, vuln_class: str, raw_entry: dict) -> ReportVulnerability:
    auth = getattr(vuln, "authentication_required", None)
    if auth is not None:
        auth = str(auth)
    return ReportVulnerability(
        id=str(vuln.ID),
        type=vuln_class,
        vulnerability_type=getattr(vuln, "vulnerability_type", None),
        title=getattr(vuln, "title", None),
        severity=effective_severity(vuln),
        confidence=getattr(vuln, "confidence", None),
        cvss=getattr(vuln, "cvss", None),
        cwe_id=getattr(vuln, "cwe_id", None),
        owasp_category=getattr(vuln, "owasp_category", None),
        externally_exploitable=getattr(vuln, "externally_exploitable", None),
        authentication_required=auth,
        merge_source=getattr(vuln, "merge_source", None),
        merged_from=list(getattr(vuln, "merged_from", None) or []),
        narrative=_narrative(vuln),
        problem_points=_problem_points(vuln, vuln_class),
        endpoints=_endpoint_entries(vuln),
        affected_entries=list(getattr(vuln, "affected_entries", None) or []),
        dataflow_steps=list(getattr(vuln, "dataflow_steps", None) or []),
        poc=_poc_block(vuln),
        evidence=_evidence(vuln),
        raw=raw_entry,
    )


def _severity_range(severities: list[str]) -> str | None:
    if not severities:
        return None
    ranked = sorted(severities, key=lambda s: SEVERITY_ORDER.get(s, 0))
    return ranked[0] if ranked[0] == ranked[-1] else f"{ranked[0]}-{ranked[-1]}"


def _quick_reference_row(vuln) -> QuickReferenceRow:
    """速查表行（spec 单源化 §5）：单元格口径复用 report_assembler（同 md 表）。

    params 存全量原样（>3 截断是渲染层的事）；endpoints 串原样，空时
    report_endpoints（②富化写回，GN 轨卡的路由锚点主信号——不落数据流文本）
    → endpoint/path 兜底归一化（对齐 _endpoint_cell，但不落 '-' placeholder——
    行是数据不是展示，渲染层管空态）。
    """
    endpoints = [str(e).strip() for e in (getattr(vuln, "endpoints", None) or [])
                 if str(e).strip()]
    if not endpoints:
        endpoints = _report_endpoint_routes(vuln)
    if not endpoints:
        fallback = (getattr(vuln, "endpoint", None)
                    or getattr(vuln, "path", None))
        if fallback:
            from supernova_core.code_index.gn_collapse import extract_endpoint
            normalized = extract_endpoint(fallback) or str(fallback).strip()
            if normalized:
                endpoints = [normalized]
    params: list[str] = []
    for p in (getattr(vuln, "affected_parameters", None) or []):
        p = str(p).strip()
        if p and p not in params:
            params.append(p)
    if not params:
        params = _report_endpoint_params(vuln)
    title = getattr(vuln, "title", None) or _type_title(vuln)
    return QuickReferenceRow(
        id=str(vuln.ID),
        title=title,
        params=params,
        endpoints=endpoints,
        severity=_severity_cell(vuln),
        verification=_verification_cell(vuln),
        confidence=_confidence_cell(vuln),
    )


def _quick_reference(queues_by_class: dict[str, list]) -> list[QuickReferenceRow]:
    """速查表行集：类序对齐 CLASS_CONFIG + 类内 severity 降序（同档稳定保序），
    与 render_summary_table 行序完全一致。"""
    rows: list[QuickReferenceRow] = []
    ordered = [c for c in CLASS_CONFIG if c in queues_by_class]
    ordered += [c for c in queues_by_class if c not in CLASS_CONFIG]
    for vuln_class in ordered:
        ranked = sorted(
            queues_by_class[vuln_class],
            key=lambda v: -SEVERITY_ORDER.get(effective_severity(v), 0))
        rows.extend(_quick_reference_row(v) for v in ranked)
    return rows


def _key_findings(vulns: list[ReportVulnerability]) -> str | None:
    """类型汇总关键发现（spec 单源化 §5）：每类 top severity 卡（≤3 张）
    「ID 标题」串、「；」拼接——确定性产，摘要 agent 可覆写。"""
    ranked = sorted(
        vulns, key=lambda v: -SEVERITY_ORDER.get(v.severity or "", 0))
    items = []
    for v in ranked[:3]:
        title = v.title or _type_title(v)
        items.append(f"{v.id} {title}".strip())
    return "；".join(items) if items else None


def _build_stats(vulns_by_class: dict[str, list[ReportVulnerability]]) -> ReportStats:
    by_type: dict[str, TypeStats] = {}
    by_severity: dict[str, int] = {}
    for vuln_class, vulns in vulns_by_class.items():
        severities = [v.severity for v in vulns if v.severity]
        by_type[vuln_class] = TypeStats(
            count=len(vulns), severity_range=_severity_range(severities),
            key_findings=_key_findings(vulns))
        for sev in severities:
            by_severity[sev] = by_severity.get(sev, 0) + 1
    return ReportStats(by_type=by_type, by_severity=by_severity)


async def _read_attack_chains(deliverables_path) -> list[AttackChainEntry]:
    chains_path = resolve_intermediate(deliverables_path, "attack_chains.json")
    if chains_path is None or not await async_path_exists(chains_path):
        return []
    try:
        data = json.loads(await async_read_file(chains_path))
    except (json.JSONDecodeError, OSError, ValueError):
        return []
    entries = []
    for chain in (data.get("chains") or []):
        if not isinstance(chain, dict):
            continue
        entries.append(AttackChainEntry(
            id=str(chain.get("id", "")),
            steps=list(chain.get("steps") or []),
            narrative=chain.get("description"),
        ))
    return entries


async def build_report_data(
    deliverables_path, scan_meta: ScanMeta,
) -> ReportData:
    """读 SSOT queue 组装 ReportData（确定性；无 LLM）。"""
    vulns_by_class: dict[str, list[ReportVulnerability]] = {}
    queue_vulns_by_class: dict[str, list] = {}
    for vuln_class, cfg in CLASS_CONFIG.items():
        queue_path = resolve_intermediate(deliverables_path, cfg.queue_file)
        if queue_path is None or not await async_path_exists(queue_path):
            continue
        try:
            content = await async_read_file(queue_path)
            parsed = VulnerabilityQueue.parse_lenient(
                content, vuln_class=vuln_class)
        except Exception as exc:  # noqa: BLE001 — 缺一类不致命
            logger.warning("report_data: queue %s unreadable: %s",
                           cfg.queue_file, exc)
            continue
        report_vulns = []
        for vuln in parsed.queue.vulnerabilities:
            raw = vuln.model_dump(exclude_none=True)
            report_vulns.append(_report_vulnerability(vuln, vuln_class, raw))
        vulns_by_class[vuln_class] = report_vulns
        queue_vulns_by_class[vuln_class] = list(parsed.queue.vulnerabilities)

    all_vulns = [v for vs in vulns_by_class.values() for v in vs]
    return ReportData(
        scan=scan_meta,
        stats=_build_stats(vulns_by_class),
        vulnerabilities=all_vulns,
        attack_chains=await _read_attack_chains(deliverables_path),
        quick_reference=_quick_reference(queue_vulns_by_class),
    )


async def write_report_data(report_data: ReportData, path) -> None:
    """JSON 落盘（ensure_ascii=False，中文原样）。"""
    payload = json.dumps(
        report_data.model_dump(mode="json"), ensure_ascii=False, indent=2)
    await async_write_file(path, payload)
