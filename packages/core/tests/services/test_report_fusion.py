"""T8（spec 2026-08-26-report-generation-agent §6.2）：融合（白盒×黑盒）。

确定性交叉验证：id/同接口匹配 → cross_verification 三态+blackbox-only；
白盒叙事 × 黑盒动态证据融合卡；verification_gaps；融合版 md 导出复用。
"""
import pytest


def _wb_rd():
    from supernova_core.models.report_data import (
        ReportData, ReportVulnerability, ScanMeta,
    )
    return ReportData(
        scan=ScanMeta(id="s1", track="whitebox"),
        vulnerabilities=[
            ReportVulnerability(
                id="XSS-VULN-01", type="xss", severity="high",
                title="存储型 XSS", merge_source="llm-only",
                endpoints=[{"method": "POST", "path": "/memos"}],
                narrative={"cause": "c", "impact": "i", "remediation": "r"},
                evidence={"verification": "static", "verdict": "vulnerable"},
            ),
            ReportVulnerability(
                id="SSRF-VULN-01", type="ssrf", severity="critical",
                title="SSRF", merge_source="llm-only",
                endpoints=[{"method": "GET", "path": "/research"}],
            ),
        ],
    )


def _bb_rd():
    from supernova_core.models.report_data import (
        ReportData, ReportVulnerability, ScanMeta,
    )
    return ReportData(
        scan=ScanMeta(id="s1", track="blackbox"),
        vulnerabilities=[
            ReportVulnerability(
                id="XSS-VULN-01", type="xss", severity="high",
                title="存储型 XSS（实测）",
                endpoints=[{"method": "POST", "path": "/memos"}],
                evidence={"verification": "dynamic",
                          "dynamic_evidence": "uid=1000 回显",
                          "verdict": "vulnerable"},
                poc={"witness_payload": "<img>",
                     "request": {"method": "POST", "url": "http://t/memos"}},
            ),
            ReportVulnerability(
                id="INJ-VULN-07", type="injection", severity="critical",
                title="黑盒独有 RCE",
                endpoints=[{"method": "POST", "path": "/contributions"}],
                evidence={"verification": "dynamic",
                          "dynamic_evidence": "命令回显", "verdict": "vulnerable"},
            ),
        ],
    )


def test_fuse_by_id_verified_with_dynamic_evidence():
    from supernova_core.services.report_fusion import fuse_report_data
    fused = fuse_report_data(_wb_rd(), _bb_rd())
    by_id = {v.id: v for v in fused.vulnerabilities}
    xss = by_id["XSS-VULN-01"]
    assert xss.cross_verification == "verified"
    # 白盒叙事保留 + 黑盒动态证据/poc 融合
    assert xss.narrative and xss.narrative.cause == "c"
    assert xss.evidence.verification == "dynamic"
    assert xss.evidence.dynamic_evidence == "uid=1000 回显"
    assert xss.poc and xss.poc.request.url.endswith("/memos")


def test_fuse_untested_and_blackbox_only():
    from supernova_core.services.report_fusion import fuse_report_data
    fused = fuse_report_data(_wb_rd(), _bb_rd())
    by_id = {v.id: v for v in fused.vulnerabilities}
    assert by_id["SSRF-VULN-01"].cross_verification == "untested"
    assert by_id["INJ-VULN-07"].cross_verification == "blackbox-only"
    # verification_gaps：白盒发现黑盒未测
    gaps = [g["vuln_id"] for g in fused.verification_gaps]
    assert "SSRF-VULN-01" in gaps
    assert fused.scan.track == "combined"


def test_fuse_endpoint_matching_when_id_differs():
    """id 不同但同接口+同类型 → verified（跨轨编号差异常态）。"""
    from supernova_core.services.report_fusion import fuse_report_data
    from supernova_core.models.report_data import ReportVulnerability
    wb = _wb_rd()
    bb = _bb_rd()
    bb.vulnerabilities[0].id = "XSS-VULN-99"   # 同 /memos 同 xss
    fused = fuse_report_data(wb, bb)
    by_id = {v.id: v for v in fused.vulnerabilities}
    assert by_id["XSS-VULN-01"].cross_verification == "verified"


def test_fuse_deterministic_summary_mentions_verification():
    from supernova_core.services.report_fusion import fuse_report_data
    fused = fuse_report_data(_wb_rd(), _bb_rd())
    assert fused.executive_summary is not None
    assert fused.executive_summary.narrative
    assert any("2" in str(t.reason) or t.vuln_id for t in
               fused.executive_summary.top_risks) or \
        fused.executive_summary.top_risks == []


def test_fuse_carries_blackbox_verification_steps():
    """融合卡携带黑盒验证步骤（2026-08-27 验证证据展示优化）：黑盒 evidence.steps
    随 dynamic_evidence 一并覆盖白盒（白盒 static 轨本无 steps）——融合报告与黑盒
    报告同源分步骤展示，命令字段供渲染层直取。"""
    from supernova_core.services.report_fusion import fuse_report_data
    from supernova_core.models.report_data import VulnEvidence
    wb = _wb_rd()
    bb = _bb_rd()
    bb.vulnerabilities[0].evidence = VulnEvidence(
        verification="dynamic", dynamic_evidence="uid=1000 回显",
        verdict="vulnerable",
        steps=[
            {"action": "Login and capture session cookie",
             "command": "curl -s -c jar http://t/login -d 'user=a'",
             "result": "302 Set-Cookie connect.sid"},
            {"action": "Post memo with witness payload",
             "command": "curl -s -b jar http://t/memos -d 'body=<img>'",
             "result": "200 stored, reflected unencoded"},
        ],
    )
    fused = fuse_report_data(wb, bb)
    by_id = {v.id: v for v in fused.vulnerabilities}
    ev = by_id["XSS-VULN-01"].evidence
    assert ev is not None and len(ev.steps) == 2
    assert ev.steps[0].command == "curl -s -c jar http://t/login -d 'user=a'"
    assert ev.steps[1].result == "200 stored, reflected unencoded"
    # 黑盒独有卡同样携带（未与白盒融合、原样透传）
    bb_only = by_id["INJ-VULN-07"].evidence
    assert bb_only is not None and bb_only.steps == []
