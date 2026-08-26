"""融合（白盒 × 黑盒）report_data（spec 2026-08-26-report-generation-agent §6.2）。

确定性交叉验证：白盒卡 ↔ 黑盒卡按 id → (type, 接口 path) 两级匹配 →
cross_verification 三态（verified / untested / failed-to-verify）+ 黑盒独有
第四态（blackbox-only）。融合卡 = 白盒叙事根因 × 黑盒动态证据/实测 POC。

LLM 融合叙事为可选增强（调用方传 llm_fn）；本模块核心纯确定性——
融合 agent 失败时交叉验证表与三态仍然完整产出。
"""
import logging

from supernova_core.models.report_data import (
    ExecutiveSummary,
    ReportData,
    ReportVulnerability,
    ScanMeta,
    TopRisk,
    VulnEvidence,
)

logger = logging.getLogger(__name__)


def _endpoint_key(v: ReportVulnerability) -> tuple[str, str] | None:
    """(type, 归一 path) 匹配键——接口表首个 path 的小写归一。"""
    for e in v.endpoints:
        path = (e.path or "").strip().lower()
        if path:
            return (v.type, path)
    return None


def _fuse_card(wb: ReportVulnerability, bb: ReportVulnerability,
               cross: str) -> ReportVulnerability:
    """融合卡：白盒叙事保留，黑盒动态证据/实测 POC 覆盖/补齐。"""
    severity = wb.severity or bb.severity
    evidence = (wb.evidence or VulnEvidence()).model_copy(deep=True)
    bb_ev = bb.evidence
    if bb_ev is not None:
        # 动态证据优先（实测 > 静态推断）；verdict 以黑盒实测为准
        evidence.verification = "dynamic"
        evidence.dynamic_evidence = bb_ev.dynamic_evidence
        evidence.verdict = bb_ev.verdict or evidence.verdict
        evidence.notes = bb_ev.notes or evidence.notes
    poc = bb.poc or wb.poc
    endpoints = wb.endpoints or bb.endpoints
    return wb.model_copy(update={
        "severity": severity,
        "evidence": evidence,
        "poc": poc,
        "endpoints": endpoints,
        "cross_verification": cross,
    })


def _deterministic_fusion_summary(fused: list[ReportVulnerability],
                                  gaps_count: int) -> ExecutiveSummary:
    verified = sum(1 for v in fused if v.cross_verification == "verified")
    bb_only = sum(1 for v in fused if v.cross_verification == "blackbox-only")
    sev_rank = {"critical": 3, "high": 2, "medium": 1, "low": 0}
    ranked = sorted(fused, key=lambda v: -sev_rank.get(v.severity or "medium", 0))
    top_level = ranked[0].severity if ranked else None
    risk = {"critical": "极高", "high": "高", "medium": "中", "low": "低",
            None: "低"}[top_level]
    narrative = (f"融合报告：白盒发现经黑盒实测交叉验证——{verified} 个已实证"
                 f"（verified），{gaps_count} 个白盒发现黑盒未覆盖（untested），"
                 f"{bb_only} 个黑盒独有发现。优先处置 verified 且 critical/high 项。")
    top_risks = [
        TopRisk(vuln_id=v.id, reason=v.title,
                priority="P0" if v.severity == "critical" else "P1")
        for v in ranked[:5]
    ]
    return ExecutiveSummary(narrative=narrative, risk_level=risk,
                            top_risks=top_risks,
                            remediation_order="先修已实证（verified）高危项，"
                                              "再补测 untested 缺口。")


def fuse_report_data(whitebox_rd: ReportData, blackbox_rd: ReportData,
                     llm_fn=None) -> ReportData:
    """确定性融合（+可选 LLM 叙事增强）。核心交叉验证永不依赖 LLM。"""
    bb_by_id = {v.id: v for v in blackbox_rd.vulnerabilities}
    bb_by_key: dict[tuple[str, str], ReportVulnerability] = {}
    for v in blackbox_rd.vulnerabilities:
        key = _endpoint_key(v)
        if key and key not in bb_by_key:
            bb_by_key[key] = v

    fused: list[ReportVulnerability] = []
    consumed_bb_ids: set[str] = set()
    gaps: list[dict] = []
    for wb in whitebox_rd.vulnerabilities:
        bb = bb_by_id.get(wb.id)
        if bb is None:
            key = _endpoint_key(wb)
            if key is not None:
                bb = bb_by_key.get(key)
        if bb is not None:
            cross = "verified"
            if (bb.evidence is not None
                    and bb.evidence.verdict not in (None, "vulnerable", "exploited")):
                cross = "failed-to-verify"
            consumed_bb_ids.add(bb.id)
            fused.append(_fuse_card(wb, bb, cross))
        else:
            fused.append(wb.model_copy(update={"cross_verification": "untested"}))
            gaps.append({"vuln_id": wb.id,
                         "reason": "白盒发现，黑盒未覆盖（untested）"})
    for bb in blackbox_rd.vulnerabilities:
        if bb.id not in consumed_bb_ids:
            fused.append(bb.model_copy(
                update={"cross_verification": "blackbox-only"}))

    scan = whitebox_rd.scan.model_copy(update={"track": "combined"})
    rd = ReportData(
        scan=scan,
        executive_summary=_deterministic_fusion_summary(fused, len(gaps)),
        stats=None,
        vulnerabilities=fused,
        attack_chains=whitebox_rd.attack_chains,
        verification_gaps=gaps,
    )
    return rd
