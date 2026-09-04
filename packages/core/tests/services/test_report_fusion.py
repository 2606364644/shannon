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


def test_fuse_not_covered_and_blackbox_only():
    """匹配不到 → not-covered（2026-09-03 四态拆分：原 untested 升格细分）。"""
    from supernova_core.services.report_fusion import fuse_report_data
    fused = fuse_report_data(_wb_rd(), _bb_rd())
    by_id = {v.id: v for v in fused.vulnerabilities}
    assert by_id["SSRF-VULN-01"].cross_verification == "not-covered"
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


def test_fuse_stats_by_type_with_track_counts():
    """融合 stats（2026-09-01 用户反馈「融合报告缺漏洞数预览」）：每类
    count / severity_range / key_findings + 白盒/黑盒分列数（取两侧报告
    该类卡数）+ by_severity——统计层只两列数，三态留行级速查表。"""
    from supernova_core.services.report_fusion import fuse_report_data
    fused = fuse_report_data(_wb_rd(), _bb_rd())
    assert fused.stats is not None
    bt = fused.stats.by_type
    assert bt["xss"].count == 1
    assert (bt["xss"].whitebox_count, bt["xss"].blackbox_count) == (1, 1)
    assert (bt["ssrf"].whitebox_count, bt["ssrf"].blackbox_count) == (1, 0)
    assert (bt["injection"].whitebox_count,
            bt["injection"].blackbox_count) == (0, 1)
    assert bt["xss"].severity_range == "high"
    assert bt["xss"].key_findings == "XSS-VULN-01 存储型 XSS"
    assert fused.stats.by_severity == {"high": 1, "critical": 2}


def test_fuse_quick_reference_rows_with_cross_state():
    """融合速查表：融合卡确定性派生，verification 列 = cross_verification
    中文标签（行级承载「黑盒验证后情况」）；类序对齐 CLASS_CONFIG。"""
    from supernova_core.services.report_fusion import fuse_report_data
    fused = fuse_report_data(_wb_rd(), _bb_rd())
    assert [r.id for r in fused.quick_reference] == [
        "INJ-VULN-07", "XSS-VULN-01", "SSRF-VULN-01"]
    by_id = {r.id: r for r in fused.quick_reference}
    assert by_id["XSS-VULN-01"].verification == "已实证"
    assert by_id["XSS-VULN-01"].endpoints == ["/memos"]
    assert by_id["XSS-VULN-01"].severity == "high"
    assert by_id["SSRF-VULN-01"].verification == "未覆盖"
    assert by_id["INJ-VULN-07"].verification == "黑盒独有"


def test_fuse_quick_reference_failed_to_verify_label():
    """failed-to-verify（黑盒测了但未实证，如 blocked_by_security）→
    速查表标「复验失败」。"""
    from supernova_core.services.report_fusion import fuse_report_data
    from supernova_core.models.report_data import (
        ReportData, ReportVulnerability, ScanMeta,
    )
    wb = ReportData(
        scan=ScanMeta(id="s1", track="whitebox"),
        vulnerabilities=[ReportVulnerability(
            id="SSRF-VULN-01", type="ssrf", severity="critical",
            endpoints=[{"method": "GET", "path": "/research"}])])
    bb = ReportData(
        scan=ScanMeta(id="s1", track="blackbox"),
        vulnerabilities=[ReportVulnerability(
            id="SSRF-VULN-01", type="ssrf",
            endpoints=[{"method": "GET", "path": "/research"}],
            evidence={"verification": "dynamic",
                      "verdict": "blocked_by_security",
                      "notes": "WAF 拦截"})])
    fused = fuse_report_data(wb, bb)
    assert fused.vulnerabilities[0].cross_verification == "failed-to-verify"
    assert fused.quick_reference[0].verification == "复验失败"


# ── 四态拆分（spec 2026-09-03-blackbox-verification-gap-traceability §6）──

def _bb_rd_with_interrupted():
    """黑盒报告含 interrupted 未验证卡（gaps 成卡产物）。"""
    from supernova_core.models.report_data import (
        ReportData, ReportVulnerability, ScanMeta, VulnEvidence,
    )
    return ReportData(
        scan=ScanMeta(id="s1", track="blackbox"),
        vulnerabilities=[
            ReportVulnerability(
                id="XSS-VULN-01", type="xss", severity=None,
                endpoints=[{"method": "POST", "path": "/memos"}],
                evidence=VulnEvidence(
                    verification="dynamic", verdict="interrupted",
                    notes="agent 未完成验证闭环（登记 0/15）；工具轨迹显示已对该端点发起过请求，未产出结论"),
            ),
        ],
    )


def test_fuse_interrupted_card_gets_interrupted_state():
    """interrupted 黑盒卡匹配白盒卡 → cross=interrupted（不覆盖动态证据/POC），
    notes 原因传导进融合卡 evidence.notes。"""
    from supernova_core.services.report_fusion import fuse_report_data
    wb = _wb_rd()
    fused = fuse_report_data(wb, _bb_rd_with_interrupted())
    by_id = {v.id: v for v in fused.vulnerabilities}
    xss = by_id["XSS-VULN-01"]
    assert xss.cross_verification == "interrupted"
    # 白盒静态证据保留（黑盒未出结论，无动态证据可覆盖）
    assert xss.evidence.verification == "static"
    # 未验证原因传导
    assert xss.evidence.notes and "登记 0/15" in xss.evidence.notes
    # gaps 节含真实原因
    gap = next(g for g in fused.verification_gaps if g["vuln_id"] == "XSS-VULN-01")
    assert "登记 0/15" in gap["reason"]


def test_fuse_not_covered_class_not_run():
    """类 verdicts 不存在（agent 未跑该类）→ not-covered，reason=黑盒未跑该类。"""
    from supernova_core.services.report_fusion import fuse_report_data
    wb = _wb_rd()  # SSRF-VULN-01 无黑盒卡
    fused = fuse_report_data(
        wb, _bb_rd(),
        blackbox_class_meta={"xss": {"exists": True, "ids": {"XSS-VULN-01"}}})
    by_id = {v.id: v for v in fused.vulnerabilities}
    assert by_id["SSRF-VULN-01"].cross_verification == "not-covered"
    gap = next(g for g in fused.verification_gaps if g["vuln_id"] == "SSRF-VULN-01")
    assert "未跑该类" in gap["reason"]


def test_fuse_not_covered_out_of_queue():
    """类跑了但白盒卡不在黑盒验证范围（无卡无 gap）→ not-covered，reason=不在范围。"""
    from supernova_core.services.report_fusion import fuse_report_data
    wb = _wb_rd()
    fused = fuse_report_data(
        wb, _bb_rd(),
        blackbox_class_meta={
            "xss": {"exists": True, "ids": {"XSS-VULN-01"}},
            "ssrf": {"exists": True, "ids": {"SSRF-VULN-99"}},  # 01 不在范围
        })
    by_id = {v.id: v for v in fused.vulnerabilities}
    assert by_id["SSRF-VULN-01"].cross_verification == "not-covered"
    gap = next(g for g in fused.verification_gaps if g["vuln_id"] == "SSRF-VULN-01")
    assert "不在黑盒验证范围" in gap["reason"]


def test_fuse_summary_counts_four_states():
    """摘要 narrative 含四态计数（interrupted/not-covered 分列）。"""
    from supernova_core.services.report_fusion import fuse_report_data
    fused = fuse_report_data(
        _wb_rd(), _bb_rd_with_interrupted(),
        blackbox_class_meta={"xss": {"exists": True, "ids": {"XSS-VULN-01"}}})
    n = fused.executive_summary.narrative
    assert "0 个已实证" in n
    assert "1 个中断未结论" in n
    assert "1 个未覆盖" in n
