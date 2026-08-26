"""report_data → markdown 导出（spec 2026-08-26-report-generation-agent-design §3）。

md 是 report_data.json 的确定性导出（下载/存档），与前端同源——单一事实源，
无第二次转换。卡片正文复用 findings_renderer.render_vuln_card（经 raw 重建
vuln 对象，零视觉回归）；速查表复用 render_summary_table。
"""
import logging

from supernova_core.models.queue_schemas import parse_vuln_entry
from supernova_core.models.report_data import ReportData
from supernova_core.services.findings_renderer import (
    CLASS_CONFIG,
    render_vuln_card,
)
from supernova_core.services.report_assembler import render_summary_table

logger = logging.getLogger(__name__)


def _rebuild_by_class(report_data: ReportData) -> dict[str, list]:
    """ReportData.vulnerabilities → {vuln_class: [queue vuln 对象]}（raw 重建）。"""
    by_class: dict[str, list] = {}
    for rv in report_data.vulnerabilities:
        raw = rv.raw
        if not isinstance(raw, dict):
            logger.warning("report_data 卡 %s 无 raw，md 导出跳过", rv.id)
            continue
        try:
            vuln = parse_vuln_entry(raw, rv.type)
        except Exception:  # noqa: BLE001 — 单卡畸形不拖垮整报告
            logger.warning("report_data 卡 %s raw 重建失败，md 导出跳过", rv.id)
            continue
        by_class.setdefault(rv.type, []).append(vuln)
    return by_class


def export_report_markdown(report_data: ReportData) -> str:
    """确定性导出：速查表（第一章）+ 每类结构化漏洞卡。"""
    by_class = _rebuild_by_class(report_data)
    sections: list[str] = []
    if by_class:
        sections.append(render_summary_table(by_class))
    card_sections: list[str] = []
    # 类序对齐 CLASS_CONFIG（与 assemble 的 vuln_classes 默认序一致）
    ordered = [c for c in CLASS_CONFIG if c in by_class]
    ordered += [c for c in by_class if c not in CLASS_CONFIG]
    for vuln_class in ordered:
        cards = [render_vuln_card(v, vuln_class) for v in by_class[vuln_class]]
        card_sections.append("\n\n".join(cards))
    if card_sections:
        sections.append("\n\n---\n\n".join(card_sections))
    return "\n\n---\n\n".join(sections)


def export_poc_collection(report_data: ReportData) -> str:
    """poc_collection.md 导出（spec 2026-08-26-report-single-source-rendering §4）。

    PoC 单源：从 report_data.poc 导出（raw_http 优先，request 确定性拼
    兜底），每卡 curl（```bash）+ raw_http（```http，Burp 原始报文）双
    fenced + 前置条件/预期响应/Witness 行；无 POC 卡整卡省略。
    """
    sections: list[str] = []
    for rv in report_data.vulnerabilities:
        poc = rv.poc
        if poc is None:
            continue
        lines: list[str] = [f"### {rv.id}", ""]
        title = rv.title or rv.vulnerability_type or rv.type
        if title:
            lines.extend((f"**{title}**", ""))
        if poc.preconditions:
            lines.extend((f"- 前置条件：{poc.preconditions}", ""))
        if poc.expected_response:
            indicator = poc.expected_response.indicator
            if indicator:
                lines.extend((f"- 预期响应：{indicator}", ""))
        if poc.witness_payload:
            lines.extend((f"- Witness：{poc.witness_payload}", ""))
        curl = poc.curl
        raw_http = poc.raw_http
        if not raw_http and poc.request is not None:
            req = poc.request
            head = f"{req.method} {req.url} HTTP/1.1"
            headers = "".join(f"{k}: {v}\r\n" for k, v in req.headers.items())
            raw_http = head + "\r\n" + headers + ("\r\n" + (req.body or ""))
        if curl:
            lines.extend(("```bash", curl, "```", ""))
        if raw_http:
            lines.extend(("```http", raw_http, "```"))
        sections.append("\n".join(lines))
    return "\n\n---\n\n".join(sections)
