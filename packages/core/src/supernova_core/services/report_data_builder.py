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


def _endpoint_entries(vuln) -> list[EndpointEntry]:
    """接口一体表（确定性派生）：report_endpoints（②富化写回）优先 →
    endpoints 串解析 → endpoint/path 单端兜底。"""
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
    if entries:
        return entries
    # 兜底：endpoint（归一化串）或 path 提取
    fallback_src = getattr(vuln, "endpoint", None) or getattr(vuln, "path", None)
    if fallback_src:
        from supernova_core.code_index.gn_collapse import extract_endpoint
        extracted = extract_endpoint(fallback_src)
        if extracted:
            parsed = parse_endpoint_string(extracted)
            if parsed is not None:
                return [parsed]
    return []


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


def _problem_points(vuln) -> list[ProblemPoint]:
    """report_problem_points（富化写回）→ ProblemPoint 透传（不合成不推断）。

    宽松校验与 endpoint 写回同立场：非 dict / 缺 location 的畸形条目丢弃。
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
    return points
    return None


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
        problem_points=_problem_points(vuln),
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


def _build_stats(vulns_by_class: dict[str, list[ReportVulnerability]]) -> ReportStats:
    by_type: dict[str, TypeStats] = {}
    by_severity: dict[str, int] = {}
    for vuln_class, vulns in vulns_by_class.items():
        severities = [v.severity for v in vulns if v.severity]
        by_type[vuln_class] = TypeStats(
            count=len(vulns), severity_range=_severity_range(severities))
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

    all_vulns = [v for vs in vulns_by_class.values() for v in vs]
    return ReportData(
        scan=scan_meta,
        stats=_build_stats(vulns_by_class),
        vulnerabilities=all_vulns,
        attack_chains=await _read_attack_chains(deliverables_path),
    )


async def write_report_data(report_data: ReportData, path) -> None:
    """JSON 落盘（ensure_ascii=False，中文原样）。"""
    payload = json.dumps(
        report_data.model_dump(mode="json"), ensure_ascii=False, indent=2)
    await async_write_file(path, payload)
