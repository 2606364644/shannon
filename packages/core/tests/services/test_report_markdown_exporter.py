"""report_markdown_exporter 测试（spec 2026-08-26-report-single-source-rendering §4）。

从 test_report_data.py 迁出（A/B 并发文件边界），后续渲染单点化 +
导出器三产物（comprehensive md / poc_collection.md）的测试都落本文件。
"""
import json

import pytest


async def _write_queue(deliverables, name, vulnerabilities):
    deliverables.mkdir(parents=True, exist_ok=True)
    (deliverables / name).write_text(
        json.dumps({"vulnerabilities": vulnerabilities}, ensure_ascii=False),
        encoding="utf-8")


async def test_export_report_markdown_summary_and_cards(tmp_path):
    from supernova_core.services.report_data_builder import build_report_data
    from supernova_core.services.report_markdown_exporter import (
        export_report_markdown,
    )
    from supernova_core.models.report_data import ScanMeta

    d = tmp_path / "deliverables"
    await _write_queue(d, "xss_exploitation_queue.json", [{
        "ID": "XSS-VULN-01", "vulnerability_type": "Stored",
        "externally_exploitable": True, "confidence": "high",
        "merge_source": "llm-only",
        "title": "存储型 XSS：POST /memos",
        "severity": "high",
        "notes": "路由为 isLoggedIn", "impact": "窃取会话", "remediation": "DOMPurify",
    }])
    rd = await build_report_data(d, ScanMeta(id="s1", track="whitebox"))
    md = export_report_markdown(rd)

    # 速查表 + 结构化漏洞卡节数与 JSON 一致（verify 口径）
    from supernova_core.services.report_assembler import count_vuln_headings
    assert "## 漏洞速查表" in md
    assert count_vuln_headings(md) == len(rd.vulnerabilities) == 1
    assert "### XSS-VULN-01" in md
    # 卡片正文复用 render_vuln_card（raw 重建）——零视觉回归
    assert "**漏洞成因（研判依据）**" in md
    assert "存储型 XSS：POST /memos" in md


async def test_export_report_markdown_multiple_classes(tmp_path):
    from supernova_core.services.report_data_builder import build_report_data
    from supernova_core.services.report_markdown_exporter import (
        export_report_markdown,
    )
    from supernova_core.models.report_data import ScanMeta
    from supernova_core.services.report_assembler import count_vuln_headings

    d = tmp_path / "deliverables"
    await _write_queue(d, "xss_exploitation_queue.json", [
        {"ID": f"XSS-VULN-{i:02d}", "vulnerability_type": "Stored",
         "externally_exploitable": True, "confidence": "high", "severity": "high"}
        for i in (1, 2)])
    await _write_queue(d, "ssrf_exploitation_queue.json", [
        {"ID": "SSRF-VULN-01", "vulnerability_type": "SSRF",
         "externally_exploitable": True, "confidence": "high", "severity": "critical"}])
    rd = await build_report_data(d, ScanMeta(id="s1", track="whitebox"))
    md = export_report_markdown(rd)
    assert count_vuln_headings(md) == 3
    assert "### SSRF-VULN-01" in md


def test_export_poc_collection_double_fence(tmp_path=None):
    """poc_collection.md 导出（spec §4 PoC 单源）：每卡 curl（```bash）+
    raw_http（```http，Burp 原始报文）双 fenced，无 POC 卡省略。"""
    from supernova_core.services.report_markdown_exporter import (
        export_poc_collection,
    )
    from supernova_core.models.report_data import (
        PocBlock, PocRequest, ReportData, ReportVulnerability, ScanMeta,
    )

    rd = ReportData(
        scan=ScanMeta(id="s1", track="whitebox"),
        vulnerabilities=[
            ReportVulnerability(
                id="XSS-VULN-01", type="xss", title="存储型 XSS",
                poc=PocBlock(
                    curl="curl -X POST http://t/memos -d 'memo=<img>'",
                    raw_http="POST /memos HTTP/1.1\r\nHost: t\r\n\r\nmemo=<img>",
                    request=PocRequest(method="POST", url="http://t/memos"),
                )),
            ReportVulnerability(id="AUTH-VULN-01", type="auth"),  # 无 POC 卡
        ])
    md = export_poc_collection(rd)
    assert "XSS-VULN-01" in md
    assert "```bash" in md and "curl -X POST" in md
    assert "```http" in md and "POST /memos HTTP/1.1" in md
    assert "AUTH-VULN-01" not in md  # 无 POC 卡不出现


def test_export_poc_collection_empty(tmp_path=None):
    from supernova_core.services.report_markdown_exporter import (
        export_poc_collection,
    )
    from supernova_core.models.report_data import ReportData, ScanMeta

    rd = ReportData(scan=ScanMeta(id="s1", track="whitebox"))
    md = export_poc_collection(rd)
    assert isinstance(md, str)
    assert "```bash" not in md
