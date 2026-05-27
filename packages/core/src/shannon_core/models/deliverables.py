from enum import Enum

class DeliverableType(str, Enum):
    CODE_ANALYSIS = "CODE_ANALYSIS"
    RECON = "RECON"
    INJECTION_ANALYSIS = "INJECTION_ANALYSIS"
    XSS_ANALYSIS = "XSS_ANALYSIS"
    AUTH_ANALYSIS = "AUTH_ANALYSIS"
    AUTHZ_ANALYSIS = "AUTHZ_ANALYSIS"
    SSRF_ANALYSIS = "SSRF_ANALYSIS"

DELIVERABLE_FILENAMES: dict[DeliverableType, str] = {
    DeliverableType.CODE_ANALYSIS: "pre_recon_deliverable.md",
    DeliverableType.RECON: "recon_deliverable.md",
    DeliverableType.INJECTION_ANALYSIS: "injection_analysis_deliverable.md",
    DeliverableType.XSS_ANALYSIS: "xss_analysis_deliverable.md",
    DeliverableType.AUTH_ANALYSIS: "auth_analysis_deliverable.md",
    DeliverableType.AUTHZ_ANALYSIS: "authz_analysis_deliverable.md",
    DeliverableType.SSRF_ANALYSIS: "ssrf_analysis_deliverable.md",
}
