"""report_data → markdown 导出（spec 2026-08-26-report-single-source-rendering §4）。

md 是 report_data.json 的确定性导出（下载/存档），与前端同源——单一事实源，
无第二次转换。卡片正文复用 findings_renderer.render_vuln_card（经 ``_VulnView``
合并视图，零视觉回归）；速查表吃 ``quick_reference``（渲染层纯渲染，不现算）。

节序（comprehensive md）：执行摘要 → 类型汇总 → 速查表 → 分类七节卡 →
攻击链 → GN 判定注记（原 report-executive 改写 / inject_* 注入 / 速查表拼装
三条 md 链路在此收编为单点导出）。
"""
import logging

from supernova_core.i18n import Messages, current_lang
from supernova_core.models.report_data import ReportData, ReportVulnerability
from supernova_core.services.findings_renderer import (
    _M as _FINDINGS_MESSAGES,
)
from supernova_core.services.findings_renderer import (
    CLASS_CONFIG,
    render_vuln_card,
)

logger = logging.getLogger(__name__)

# 导出器标签双语（zh/en，跟随 SUPERNOVA_AGENT_NARRATION_LANG；速查表/攻击链
# 文案对齐 report_assembler 原措辞——md 视觉基准 101619）。
_M = Messages({
    "exec_summary_h2": {"zh": "## 执行摘要", "en": "## Executive Summary"},
    "risk_level_label": {"zh": "- **风险等级:**", "en": "- **Risk Level:**"},
    "remediation_order_label": {"zh": "- **处置顺序:**",
                                "en": "- **Remediation Order:**"},
    "top_risks_h3": {"zh": "### 最高风险", "en": "### Top Risks"},
    "type_summary_h2": {"zh": "## 漏洞类型汇总",
                        "en": "## Vulnerability Type Summary"},
    "severity_range_label": {"zh": "- **严重度范围:**",
                             "en": "- **Severity Range:**"},
    "key_findings_label": {"zh": "- **关键发现:**", "en": "- **Key Findings:**"},
    "summary_table_h2": {"zh": "## 漏洞速查表",
                         "en": "## Vulnerability Summary Table"},
    "summary_table_header": {
        "zh": "| ID | 漏洞 | 接口 | 参数 | 严重度 | 验证 | 置信度 |",
        "en": ("| ID | Vulnerability | Endpoint | Parameters | Severity | "
               "Verification | Confidence |"),
    },
    "params_join": {"zh": "、", "en": ", "},
    "chain_section_h2": {"zh": "## 攻击链（多步利用路径）",
                         "en": "## Attack Chains (Multi-step Exploitation Paths)"},
    "label_steps": {"zh": "- **步骤:**", "en": "- **Steps:**"},
    "gn_status_h2": {"zh": "## GitNexus 轨判定状态",
                     "en": "## GitNexus Track Verdict Status"},
    "gn_status_line": {
        "zh": "- {vid}: GitNexus 轨判定未完成（{conf}），结果待复核",
        "en": "- {vid}: GitNexus track verdict incomplete ({conf}), pending review",
    },
    "poc_doc_title": {"zh": "# 可利用漏洞 PoC 集合（{track}）",
                      "en": "# Exploitable PoC Collection ({track})"},
    "poc_data_source": {
        "zh": "> 数据源：report_data.json ｜ 共 {n} 条 PoC",
        "en": "> Source: report_data.json | {n} PoCs"},
    "poc_overview_h2": {"zh": "## 概览", "en": "## Overview"},
    "poc_detail_h2": {"zh": "## 详细 PoC", "en": "## Detailed PoCs"},
    "poc_curl_label": {"zh": "**curl:**", "en": "**curl:**"},
    "poc_burp_label": {"zh": "**Burp Repeater (raw):**",
                       "en": "**Burp Repeater (raw):**"},
    "poc_preconditions": {"zh": "前置条件", "en": "Preconditions"},
    "poc_expected": {"zh": "预期响应", "en": "Expected Response"},
    "poc_witness": {"zh": "Witness", "en": "Witness"},
    "poc_auth_unknown": {"zh": "未知", "en": "unknown"},
    "poc_conf_unknown": {"zh": "待复核", "en": "Pending Review"},
    "poc_empty": {
        "zh": "本次扫描无可生成 PoC 的 HTTP 漏洞，未生成 PoC。",
        "en": "No HTTP vulnerabilities with generatable PoCs in this scan."},
})

_SUMMARY_TABLE_SEP = "|---|---|---|---|---|---|---|"
_PLACEHOLDER = "-"
_TRACK_CN = {"whitebox": "白盒", "blackbox": "黑盒", "combined": "融合"}
_CONF_CN = {"high": "高", "medium": "中", "low": "低",
            "unadjudicated": "未判定（判定通道失败）"}

# rv.evidence.verification 枚举 → queue verification 值（渲染函数比较口径）
_VERIFICATION_TO_QUEUE = {"static": "static_analysis",
                          "dynamic": "dynamically_verified"}


class _VulnView:
    """ReportVulnerability → queue-vuln duck-typing 视图（渲染函数零改动）。

    渲染函数族（render_vuln_card 等）用 ``vuln.ID`` / ``vuln.notes`` /
    ``vuln.report_poc`` 访问 queue vuln 对象。视图两层取值：``rv.raw``
    （queue entry dump，key 即字段名——VulnFinding 字段名就是 ID）优先；
    raw 缺失时 ReportVulnerability 显式结构化字段兜底映射（无 raw 的手工
    fixture 也能渲染）。
    """

    def __init__(self, rv: ReportVulnerability):
        object.__setattr__(self, "_rv", rv)
        object.__setattr__(self, "_raw",
                           rv.raw if isinstance(rv.raw, dict) else {})

    def __getattr__(self, name: str):
        raw = object.__getattribute__(self, "_raw")
        if name in raw:
            return raw[name]
        return self._from_structured(name)

    def _from_structured(self, name: str):
        rv = object.__getattribute__(self, "_rv")
        if name == "ID":
            return rv.id
        if name == "report_poc":
            return rv.poc.model_dump() if rv.poc is not None else None
        if name == "report_problem_points":
            return [p.model_dump() for p in rv.problem_points] or None
        if name == "report_endpoints":
            return [e.model_dump(exclude_none=True) for e in rv.endpoints] or None
        if name == "endpoints":
            # EndpointEntry → "METHOD /path (role, auth)" 串（解析链形态）
            out = []
            for e in rv.endpoints:
                s = (f"{e.method} " if e.method else "") + e.path
                paren = ", ".join(x for x in (e.role, e.auth) if x)
                if paren:
                    s += f" ({paren})"
                out.append(s)
            return out or None
        if name == "notes":
            return rv.narrative.cause if rv.narrative else None
        if name == "impact":
            return rv.narrative.impact if rv.narrative else None
        if name == "remediation":
            return rv.narrative.remediation if rv.narrative else None
        if name == "verification":
            if rv.evidence is None:
                return None
            return _VERIFICATION_TO_QUEUE.get(rv.evidence.verification)
        if name == "verdict":
            return rv.evidence.verdict if rv.evidence else None
        simple = {
            "title": rv.title, "severity": rv.severity,
            "confidence": rv.confidence, "cwe_id": rv.cwe_id,
            "merge_source": rv.merge_source, "merged_from": rv.merged_from,
            "affected_entries": rv.affected_entries,
            "dataflow_steps": rv.dataflow_steps,
            "authentication_required": rv.authentication_required,
            "vulnerability_type": rv.vulnerability_type,
            "externally_exploitable": rv.externally_exploitable,
            "cvss": rv.cvss, "owasp_category": rv.owasp_category,
        }
        if name in simple:
            return simple[name]
        raise AttributeError(name)


def _rebuild_by_class(report_data: ReportData) -> dict[str, list]:
    """ReportData.vulnerabilities → {vuln_class: [_VulnView]}（合并视图）。"""
    by_class: dict[str, list] = {}
    for rv in report_data.vulnerabilities:
        if not isinstance(rv.raw, dict) and not _view_has_payload(rv):
            logger.warning("report_data 卡 %s 无 raw 且无结构化字段，md 导出跳过", rv.id)
            continue
        by_class.setdefault(rv.type, []).append(_VulnView(rv))
    return by_class


def _view_has_payload(rv: ReportVulnerability) -> bool:
    """无 raw 的卡也要有起码的结构化素材（title/narrative 任一）才可渲染。"""
    return bool(rv.title or rv.narrative or rv.problem_points or rv.poc)


def _cell_escape(text: str) -> str:
    """md 表格单元格转义（| 破列）。"""
    return str(text).replace("|", "\\|")


def _exec_summary_lines(report_data: ReportData) -> str | None:
    es = report_data.executive_summary
    if es is None:
        return None
    if not any((es.narrative, es.risk_level, es.top_risks, es.remediation_order)):
        return None
    lines: list[str] = [_M.get("exec_summary_h2"), ""]
    if es.narrative:
        lines.extend((es.narrative, ""))
    zh = current_lang() == "zh"
    if es.risk_level:
        lines.append(f"{_M.get('risk_level_label')} {es.risk_level}")
    if es.remediation_order:
        lines.append(f"{_M.get('remediation_order_label')} {es.remediation_order}")
    if es.top_risks:
        lines.extend((_M.get("top_risks_h3"), ""))
        for t in es.top_risks:
            dash = "—" if zh else "—"
            line = f"- {t.vuln_id} {dash} {t.reason or ''}".rstrip()
            if t.priority:
                line += f"（{t.priority}）" if zh else f" ({t.priority})"
            lines.append(line)
    return "\n".join(lines)


def _type_class_heading(vuln_class: str) -> str:
    cfg = CLASS_CONFIG.get(vuln_class)
    if cfg is not None:
        return _FINDINGS_MESSAGES.get(cfg.heading)
    return str(vuln_class)


def _type_summary_lines(report_data: ReportData) -> str | None:
    stats = report_data.stats
    if stats is None or not stats.by_type:
        return None
    lines: list[str] = [_M.get("type_summary_h2"), ""]
    for vuln_class, ts in stats.by_type.items():
        heading = _type_class_heading(vuln_class)
        lines.extend((f"### {heading}（{ts.count}）", ""))
        if ts.severity_range:
            lines.append(f"{_M.get('severity_range_label')} {ts.severity_range}")
        if ts.key_findings:
            lines.append(f"{_M.get('key_findings_label')} {ts.key_findings}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _render_quick_reference_table(rows) -> str:
    """速查表（吃 quick_reference，渲染层纯渲染——空判定在调用方）。"""
    join = _M.get("params_join")
    lines: list[str] = [_M.get("summary_table_h2"), "",
                        _M.get("summary_table_header"), _SUMMARY_TABLE_SEP]
    for r in rows:
        title = r.title or _PLACEHOLDER
        endpoints = join.join(r.endpoints) if r.endpoints else _PLACEHOLDER
        params = join.join(r.params) if r.params else _PLACEHOLDER
        severity = r.severity or _PLACEHOLDER
        verification = r.verification or _PLACEHOLDER
        confidence = r.confidence or _PLACEHOLDER
        lines.append(
            f"| {_cell_escape(r.id)} | {_cell_escape(title)} "
            f"| {_cell_escape(endpoints)} | {_cell_escape(params)} "
            f"| {_cell_escape(severity)} | {_cell_escape(verification)} "
            f"| {_cell_escape(confidence)} |")
    return "\n".join(lines)


def _attack_chain_lines(chains) -> str | None:
    """攻击链节（原 report_assembler.render_attack_chains 读文件版收编：
    吃内存 AttackChainEntry，节标题/步骤行形态对齐——链名/类型/严重度行
    AttackChainEntry 无对应字段，省略）。"""
    if not chains:
        return None
    lines: list[str] = ["", _M.get("chain_section_h2"), ""]
    for i, chain in enumerate(chains, start=1):
        cid = chain.id or f"chain-{i}"
        lines.extend((f"### {cid}", ""))
        if chain.narrative:
            lines.extend((chain.narrative, ""))
        lines.append(_M.get("label_steps"))
        for step in chain.steps:
            if not isinstance(step, dict):
                continue
            order = step.get("order", "")
            endpoint = step.get("endpoint", "")
            method = step.get("method", "-")
            sd = step.get("description", "")
            lines.append(f"  {order}. {endpoint} ({method}) — {sd}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _gn_track_status_lines(report_data: ReportData) -> str | None:
    """GN 判定注记（原 inject_gitnexus_track_status md 注入收编）：数据源改吃
    卡级字段——gitnexus-only 且判定通道失败（unadjudicated）的卡逐条列出；
    无此类卡整节省略。"""
    pending = [rv for rv in report_data.vulnerabilities
               if rv.merge_source == "gitnexus-only"
               and rv.confidence == "unadjudicated"]
    if not pending:
        return None
    lines: list[str] = [_M.get("gn_status_h2"), ""]
    for rv in pending:
        lines.append(_M.get("gn_status_line",
                            vid=rv.id, conf=rv.confidence or "-"))
    return "\n".join(lines)


def export_report_markdown(report_data: ReportData) -> str:
    """确定性导出（节序 spec §4）：执行摘要 → 类型汇总 → 速查表 →
    分类七节卡 → 攻击链 → GN 判定注记。空整节省略。"""
    by_class = _rebuild_by_class(report_data)
    sections: list[str] = []
    for section in (
        _exec_summary_lines(report_data),
        _type_summary_lines(report_data),
        _render_quick_reference_table(report_data.quick_reference)
        if report_data.quick_reference else None,
        _attack_chain_lines(report_data.attack_chains),
        _gn_track_status_lines(report_data),
    ):
        if section:
            sections.append(section)
    card_sections: list[str] = []
    # 类序对齐 CLASS_CONFIG（与 assemble 的 vuln_classes 默认序一致）
    ordered = [c for c in CLASS_CONFIG if c in by_class]
    ordered += [c for c in by_class if c not in CLASS_CONFIG]
    for vuln_class in ordered:
        cards = [render_vuln_card(v, vuln_class) for v in by_class[vuln_class]]
        card_sections.append("\n\n".join(cards))
    if card_sections:
        sections.insert(3, "\n\n---\n\n".join(card_sections))
    return "\n\n---\n\n".join(sections)


def _conf_display(conf: str | None) -> str:
    return _CONF_CN.get((conf or "").lower(), _M.get("poc_conf_unknown"))


def _primary_endpoint(rv: ReportVulnerability) -> tuple[str, str]:
    """(method, path)：首接口 → poc.request 兜底。"""
    if rv.endpoints:
        e = rv.endpoints[0]
        return (e.method or "-", e.path)
    if rv.poc is not None and rv.poc.request is not None:
        req = rv.poc.request
        path = req.url.split("://", 1)[-1].split("/", 1)
        return (req.method, "/" + path[1] if len(path) > 1 else "/")
    return ("-", "-")


def export_poc_collection(report_data: ReportData) -> str:
    """poc_collection.md 导出（spec §4 PoC 单源）。

    从 report_data.poc 导出（raw_http 优先，request 确定性拼兜底），节结构
    对齐 poc_generator.render_poc_md：文档标题 + 概览表 + 详细 PoC
    （``**curl:**`` ```bash 与 ``**Burp Repeater (raw):**`` ```http 双标签
    双 fence）；无 POC 卡整卡省略。
    """
    track_cn = _TRACK_CN.get(report_data.scan.track, report_data.scan.track)
    title = _M.get("poc_doc_title", track=track_cn)
    cards = [rv for rv in report_data.vulnerabilities if rv.poc is not None]
    if not cards:
        return f"{title}\n\n{_M.get('poc_empty')}\n"
    header = (f"{title}\n\n{_M.get('poc_data_source', n=len(cards))}\n")

    overview: list[str] = [
        "", _M.get("poc_overview_h2"), "",
        "| ID | 类型 | 路径 | 认证 | 置信度 |",
        "|----|------|------|------|--------|",
    ]
    for rv in cards:
        method, path = _primary_endpoint(rv)
        auth = rv.authentication_required or _M.get("poc_auth_unknown")
        overview.append(
            f"| {rv.id} | {rv.type} | {method} {path} | {auth} "
            f"| {_conf_display(rv.confidence)} |")

    detail: list[str] = ["", _M.get("poc_detail_h2"), ""]
    for rv in cards:
        poc = rv.poc
        method, path = _primary_endpoint(rv)
        detail.append(f"### {rv.id} · {rv.type} @ {method} {path}")
        if rv.title:
            detail.extend((f"**{rv.title}**", ""))
        meta: list[str] = []
        if poc.preconditions:
            meta.append(f"{_M.get('poc_preconditions')}：{poc.preconditions}")
        if poc.expected_response and poc.expected_response.indicator:
            meta.append(
                f"{_M.get('poc_expected')}：{poc.expected_response.indicator}")
        if poc.witness_payload:
            meta.append(f"{_M.get('poc_witness')}：{poc.witness_payload}")
        if meta:
            detail.extend((" ｜ ".join(meta), ""))
        curl = poc.curl
        raw_http = poc.raw_http
        if not raw_http and poc.request is not None:
            req = poc.request
            head = f"{req.method} {req.url} HTTP/1.1"
            headers = "".join(f"{k}: {v}\r\n" for k, v in req.headers.items())
            raw_http = head + "\r\n" + headers + ("\r\n" + (req.body or ""))
        if curl:
            detail.extend((_M.get("poc_curl_label"), "```bash", curl, "```", ""))
        if raw_http:
            detail.extend((_M.get("poc_burp_label"), "```http", raw_http, "```"))
        detail.extend(("", "---", ""))
    return header + "\n".join(overview) + "\n" + "\n".join(detail)
