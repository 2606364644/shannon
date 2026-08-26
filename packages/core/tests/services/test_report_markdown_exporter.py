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
    # 单源化（spec §4）：速查表吃 quick_reference（builder 产物），测试塞入
    from supernova_core.models.report_data import QuickReferenceRow
    rd.quick_reference = [QuickReferenceRow(
        id="XSS-VULN-01", title="存储型 XSS：POST /memos",
        params=["memo (body)"], endpoints=["POST /memos"],
        severity="high", verification="静态分析", confidence="高")]
    md = export_report_markdown(rd)

    # 速查表 + 结构化漏洞卡节数与 JSON 一致（verify 口径）
    from supernova_core.services.report_assembler import count_vuln_headings
    assert "## 漏洞速查表" in md
    assert count_vuln_headings(md) == len(rd.vulnerabilities) == 1
    assert "### XSS-VULN-01" in md
    # 卡片正文复用 render_vuln_card（视图路径）——零视觉回归
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


# ---------- spec 2026-08-26-report-single-source-rendering §4：渲染单点化 + 节序 ----------

def _full_report_data():
    """全字段 fixture：摘要/类型汇总/速查表/七节卡/攻击链/GN 未判定卡。"""
    from supernova_core.models.report_data import (
        AttackChainEntry, EndpointEntry, ExecutiveSummary, PocBlock,
        QuickReferenceRow, ReportData, ReportStats, ReportVulnerability,
        ScanMeta, TopRisk, TypeStats, VulnNarrative,
    )
    return ReportData(
        scan=ScanMeta(id="s1", track="whitebox"),
        executive_summary=ExecutiveSummary(
            narrative="综合评估：存在高危注入与存储型 XSS。",
            risk_level="高风险",
            top_risks=[TopRisk(vuln_id="INJ-GN-01", reason="eval 直达", priority="P0")],
            remediation_order="先修注入，再修 XSS。"),
        stats=ReportStats(
            by_type={"injection": TypeStats(
                count=2, severity_range="medium-critical",
                key_findings="eval 注入 ×1；NoSQL 注入 ×1")},
            by_severity={"critical": 1, "medium": 1}),
        quick_reference=[
            QuickReferenceRow(
                id="INJ-GN-01", title="eval 注入", params=["preTax (body)"],
                endpoints=["POST /contributions"], severity="critical",
                verification="静态分析", confidence="待复核"),
            QuickReferenceRow(
                id="XSS-VULN-01", title="存储型 XSS", params=["memo (body)"],
                endpoints=["POST /memos"], severity="high",
                verification="静态分析", confidence="高"),
        ],
        vulnerabilities=[
            ReportVulnerability(
                id="INJ-GN-01", type="injection", title="eval 注入",
                severity="critical", confidence="unadjudicated",
                merge_source="gitnexus-only",
                narrative=VulnNarrative(cause="c", impact="i", remediation="r"),
                endpoints=[EndpointEntry(method="POST", path="/contributions")],
                poc=PocBlock(
                    curl="curl -X POST http://t/contributions",
                    raw_http="POST /contributions HTTP/1.1\r\nHost: t\r\n"),
                raw={"ID": "INJ-GN-01", "title": "eval 注入",
                     "severity": "critical", "confidence": "unadjudicated",
                     "merge_source": "gitnexus-only", "notes": "c",
                     "impact": "i", "remediation": "r",
                     "verification": "static_analysis",
                     "affected_parameters": ["preTax (body)"]}),
            ReportVulnerability(
                id="XSS-VULN-01", type="xss", title="存储型 XSS",
                severity="high", confidence="high", merge_source="llm-only"),
        ],
        attack_chains=[
            AttackChainEntry(
                id="chain-1",
                steps=[{"order": 1, "endpoint": "/signup", "method": "POST",
                        "description": "注册注入 payload"}],
                narrative="注册 → 触发存储型 XSS"),
        ],
    )


def test_export_report_markdown_section_order():
    """节序（spec §4）：执行摘要 → 类型汇总 → 速查表 → 七节卡 → 攻击链 →
    GN 判定注记。"""
    from supernova_core.services.report_markdown_exporter import (
        export_report_markdown,
    )
    md = export_report_markdown(_full_report_data())
    i_summary = md.index("## 执行摘要")
    i_bytype = md.index("## 漏洞类型汇总")
    i_quick = md.index("## 漏洞速查表")
    i_card = md.index("### INJ-GN-01")
    i_chain = md.index("## 攻击链")
    i_gn = md.index("## GitNexus 轨判定状态")
    assert i_summary < i_bytype < i_quick < i_card < i_chain < i_gn


def test_quick_reference_table_renders_rows_and_omits_when_empty():
    from supernova_core.services.report_markdown_exporter import (
        export_report_markdown,
    )
    md = export_report_markdown(_full_report_data())
    # 表头（7 列对齐现速查表）+ 每卡一行
    assert "| ID | 漏洞 | 接口 | 参数 | 严重度 | 验证 | 置信度 |" in md
    assert "| INJ-GN-01 |" in md and "| XSS-VULN-01 |" in md
    assert "preTax (body)" in md  # params 入表

    rd = _full_report_data()
    rd.quick_reference = []
    md2 = export_report_markdown(rd)
    assert "## 漏洞速查表" not in md2  # 空 → 整节省略（渲染层不现算）


def test_attack_chain_section_from_report_data():
    """攻击链节收编（原 report_assembler.render_attack_chains 读文件版退役）：
    吃内存 AttackChainEntry，链 ID/叙述/步骤行同形态。"""
    from supernova_core.services.report_markdown_exporter import (
        export_report_markdown,
    )
    md = export_report_markdown(_full_report_data())
    assert "## 攻击链" in md
    assert "### chain-1" in md
    assert "注册 → 触发存储型 XSS" in md          # narrative
    assert "1. /signup (POST) — 注册注入 payload" in md  # 步骤行同原形态

    rd = _full_report_data()
    rd.attack_chains = []
    assert "## 攻击链" not in export_report_markdown(rd)


def test_gn_track_status_section_from_card_fields():
    """GN 判定注记（原 inject_gitnexus_track_status md 注入退役）：吃卡级
    merge_source/confidence——gitnexus-only 且未判定的卡逐条列出。"""
    from supernova_core.services.report_markdown_exporter import (
        export_report_markdown,
    )
    md = export_report_markdown(_full_report_data())
    assert "## GitNexus 轨判定状态" in md
    assert "INJ-GN-01" in md.split("## GitNexus 轨判定状态")[1]

    # 无 GN 未判定卡 → 整节省略
    rd = _full_report_data()
    for v in rd.vulnerabilities:
        if v.id == "INJ-GN-01":
            v.confidence = "high"
            v.raw["confidence"] = "high"
    assert "## GitNexus 轨判定状态" not in export_report_markdown(rd)


def test_exec_summary_and_type_summary_sections():
    from supernova_core.services.report_markdown_exporter import (
        export_report_markdown,
    )
    md = export_report_markdown(_full_report_data())
    assert "综合评估：存在高危注入与存储型 XSS。" in md
    assert "INJ-GN-01 — eval 直达" in md           # top_risks
    assert "先修注入，再修 XSS。" in md             # remediation_order
    assert "eval 注入 ×1；NoSQL 注入 ×1" in md      # key_findings
    assert "medium-critical" in md                  # severity_range

    # 空 executive_summary / stats → 节省略
    rd = _full_report_data()
    rd.executive_summary = None
    rd.stats = None
    md2 = export_report_markdown(rd)
    assert "## 执行摘要" not in md2
    assert "## 漏洞类型汇总" not in md2


def test_vuln_view_renders_card_without_raw():
    """_vuln_view 兜底：无 raw 的手工 ReportVulnerability 也能渲染七节卡
    （结构化字段兜底映射）。"""
    from supernova_core.services.report_markdown_exporter import (
        export_report_markdown,
    )
    from supernova_core.models.report_data import (
        PocBlock, ProblemPoint, ReportData, ReportVulnerability, ScanMeta,
        VulnNarrative,
    )
    rd = ReportData(
        scan=ScanMeta(id="s1", track="whitebox"),
        vulnerabilities=[ReportVulnerability(
            id="AUTH-VULN-01", type="auth", title="垂直越权",
            severity="high", confidence="high",
            narrative=VulnNarrative(cause="缺角色检查", impact="提权",
                                    remediation="加 RBAC"),
            problem_points=[ProblemPoint(location="a/b.js:10",
                                         description="此处缺角色校验")],
            poc=PocBlock(curl="curl http://t/admin"),
        )])
    md = export_report_markdown(rd)
    assert "### AUTH-VULN-01" in md
    assert "垂直越权" in md
    assert "缺角色检查" in md          # narrative.cause → notes 兜底
    assert "a/b.js:10" in md           # problem_points → report_problem_points
    assert "```bash" in md             # poc → report_poc 兜底
    # request 缺 → raw_http 确定性拼不出，http 块省略（burp 块非必有）
    assert "```http" not in md


def test_export_poc_collection_structure():
    """poc_collection.md 对齐 poc_generator.render_poc_md 节结构：文档标题 +
    概览表 + 详细 PoC（curl/Burp 双标签双 fence）。"""
    from supernova_core.services.report_markdown_exporter import (
        export_poc_collection,
    )
    md = export_poc_collection(_full_report_data())
    assert "# 可利用漏洞 PoC 集合（白盒）" in md
    assert "## 概览" in md
    assert "| ID | 类型 | 路径 | 认证 | 置信度 |" in md
    assert "## 详细 PoC" in md
    assert "**curl:**" in md
    assert "**Burp Repeater (raw):**" in md
    assert "```bash" in md and "```http" in md
    # XSS-VULN-01 无 poc → 不出现
    assert "XSS-VULN-01" not in md
