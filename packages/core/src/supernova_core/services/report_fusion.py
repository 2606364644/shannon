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
    QuickReferenceRow,
    ReportData,
    ReportVulnerability,
    ScanMeta,
    TopRisk,
    VulnEvidence,
)
from supernova_core.services.findings_renderer import CLASS_CONFIG
# 聚合/标题口径复用白盒 builder（勿抄逻辑——与白盒报告同口径；
# 同包跨模块复用私有函数沿 report_data_builder 复用 report_assembler
# 单元格函数的先例）
from supernova_core.services.report_data_builder import _build_stats
from supernova_core.services.report_assembler import _type_title
from supernova_core.services.severity_rules import SEVERITY_ORDER

logger = logging.getLogger(__name__)

# cross_verification → 速查表 verification 列中文标签（行级承载
# 「黑盒验证后情况」；failed 类在卡片 evidence.notes 另有失败说明）
_CROSS_LABELS = {
    "verified": "已实证",
    "failed-to-verify": "复验失败",
    "untested": "未覆盖",
    "blackbox-only": "黑盒独有",
}


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
        # 黑盒验证步骤（分步骤命令复验的底座）随动态证据带入；白盒 static 轨为空
        evidence.steps = bb_ev.steps
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


def _group_by_type(
        fused: list[ReportVulnerability]) -> dict[str, list[ReportVulnerability]]:
    """融合卡按类分组，类序对齐 CLASS_CONFIG（白盒 _quick_reference 口径），
    未知类按首现序追加。"""
    groups: dict[str, list[ReportVulnerability]] = {}
    for v in fused:
        groups.setdefault(v.type, []).append(v)
    ordered = {c: groups[c] for c in CLASS_CONFIG if c in groups}
    ordered.update({c: vs for c, vs in groups.items() if c not in CLASS_CONFIG})
    return ordered


def _fusion_stats(fused: list[ReportVulnerability],
                  whitebox_rd: ReportData,
                  blackbox_rd: ReportData):
    """融合统计（2026-09-01 用户反馈「融合报告缺漏洞数预览」）：
    count/severity_range/key_findings/by_severity 复用白盒 _build_stats 同口径
    ——渲染端（前端 StatsRow / md 类型汇总）零改动即亮；另填每类
    whitebox_count/blackbox_count 分列数（两侧报告该类卡数——含黑盒独有类
    /黑盒未测类自然为 0，从 vulnerabilities 现数不依赖输入 stats 是否已填）。"""
    from collections import Counter
    stats = _build_stats(_group_by_type(fused))
    wb_counts = Counter(v.type for v in whitebox_rd.vulnerabilities)
    bb_counts = Counter(v.type for v in blackbox_rd.vulnerabilities)
    for vuln_class, ts in stats.by_type.items():
        ts.whitebox_count = wb_counts.get(vuln_class, 0)
        ts.blackbox_count = bb_counts.get(vuln_class, 0)
    return stats


def _fusion_quick_reference(
        fused: list[ReportVulnerability]) -> list[QuickReferenceRow]:
    """融合速查表：融合卡确定性派生；类序对齐 CLASS_CONFIG + 类内
    severity 降序（白盒口径）；verification 列 = cross_verification 中文
    标签；params/endpoints 取结构化 endpoints 表展平去重。"""
    rows: list[QuickReferenceRow] = []
    for vs in _group_by_type(fused).values():
        ranked = sorted(
            vs, key=lambda v: -SEVERITY_ORDER.get(v.severity or "", 0))
        for v in ranked:
            params: list[str] = []
            endpoints: list[str] = []
            for e in v.endpoints:
                if e.path and e.path not in endpoints:
                    endpoints.append(e.path)
                for p in e.params:
                    if p and p not in params:
                        params.append(p)
            rows.append(QuickReferenceRow(
                id=v.id,
                title=v.title or _type_title(v),
                params=params,
                endpoints=endpoints,
                severity=v.severity,
                verification=_CROSS_LABELS.get(v.cross_verification,
                                               v.cross_verification),
                confidence=v.confidence,
            ))
    return rows


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
        stats=_fusion_stats(fused, whitebox_rd, blackbox_rd),
        vulnerabilities=fused,
        attack_chains=whitebox_rd.attack_chains,
        verification_gaps=gaps,
        quick_reference=_fusion_quick_reference(fused),
    )
    return rd
