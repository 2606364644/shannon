import fnmatch
from enum import Enum

from supernova_core.utils.paths import INTERMEDIATE_SUBDIR as INTERMEDIATE_SUBDIR_NAME

class DeliverableType(str, Enum):
    CODE_ANALYSIS = "CODE_ANALYSIS"
    RECON = "RECON"
    INJECTION_ANALYSIS = "INJECTION_ANALYSIS"
    XSS_ANALYSIS = "XSS_ANALYSIS"
    AUTH_ANALYSIS = "AUTH_ANALYSIS"
    AUTHZ_ANALYSIS = "AUTHZ_ANALYSIS"
    SSRF_ANALYSIS = "SSRF_ANALYSIS"
    INJECTION_EVIDENCE = "INJECTION_EVIDENCE"
    XSS_EVIDENCE = "XSS_EVIDENCE"
    AUTH_EVIDENCE = "AUTH_EVIDENCE"
    AUTHZ_EVIDENCE = "AUTHZ_EVIDENCE"
    SSRF_EVIDENCE = "SSRF_EVIDENCE"
    REPORT = "REPORT"
    CODE_INDEX = "CODE_INDEX"
    ENTRY_POINTS = "ENTRY_POINTS"

DELIVERABLE_FILENAMES: dict[DeliverableType, str] = {
    DeliverableType.CODE_ANALYSIS: "pre_recon_deliverable.md",
    DeliverableType.RECON: "recon_deliverable.md",
    DeliverableType.INJECTION_ANALYSIS: "injection_analysis_deliverable.md",
    DeliverableType.XSS_ANALYSIS: "xss_analysis_deliverable.md",
    DeliverableType.AUTH_ANALYSIS: "auth_analysis_deliverable.md",
    DeliverableType.AUTHZ_ANALYSIS: "authz_analysis_deliverable.md",
    DeliverableType.SSRF_ANALYSIS: "ssrf_analysis_deliverable.md",
    DeliverableType.INJECTION_EVIDENCE: "injection_exploitation_evidence.md",
    DeliverableType.XSS_EVIDENCE: "xss_exploitation_evidence.md",
    DeliverableType.AUTH_EVIDENCE: "auth_exploitation_evidence.md",
    DeliverableType.AUTHZ_EVIDENCE: "authz_exploitation_evidence.md",
    DeliverableType.SSRF_EVIDENCE: "ssrf_exploitation_evidence.md",
    DeliverableType.REPORT: "comprehensive_security_assessment_report.md",
    DeliverableType.CODE_INDEX: "code_index.json",
    DeliverableType.ENTRY_POINTS: "entry_points.json",
}

# ── 中间产物 tier SSOT（spec 2026-08-18 deliverables tiering）─────────────────
# 文件名模式清单（fnmatch，对 basename 匹配）：命中即中间产物。core 写侧落盘
# 位置与 web 读侧 tier 判定共用此清单；新增管线产物时在此登记，web 端零改动。
# 判据：给人看的安全结论 = 交付物（桶顶层）；机器交接/管线数据 = intermediate。
INTERMEDIATE_FILE_PATTERNS: tuple[str, ...] = (
    "code_index.json",
    "entry_points.json",
    "code_index_summary.md",
    "parameter_graph.json",
    "attack_chains*.json",
    "route_chains.json",
    "framework_analysis.json",
    "frontend_mapping.json",
    "*_llm_queue.json",
    "*_gitnexus_queue.json",
    "*_exploitation_queue.json",
    "*_exploit_verdicts.json",
    "endpoint_verify.json",
    "rule_gap_report.json",
    "source_gap_report.json",
    "storage_gap_report.json",
    "gitnexus_track_status.json",
    "audit_plan.json",
    ".*checkpoint*.json",
)

TIER_DELIVERABLE = "deliverable"
TIER_INTERMEDIATE = "intermediate"


def classify_tier(rel_path: str) -> str:
    """判定产物 tier：路径含 intermediate/ 段 → intermediate（新结构权威判据，
    模式清单未登记的新中间产物也命中）；否则按文件名模式兜底（旧结构平铺时
    queue/index 类仍归中间）；都不命中 → deliverable。

    rel_path 是相对 deliverables 根的路径（如 ``whitebox/intermediate/x.json``
    或旧结构 ``whitebox/x.json``）。
    """
    parts = rel_path.replace("\\", "/").split("/")
    if INTERMEDIATE_SUBDIR_NAME in parts:
        return TIER_INTERMEDIATE
    basename = parts[-1]
    if any(fnmatch.fnmatch(basename, pattern) for pattern in INTERMEDIATE_FILE_PATTERNS):
        return TIER_INTERMEDIATE
    return TIER_DELIVERABLE
