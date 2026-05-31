from enum import Enum

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
}
