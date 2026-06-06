from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict

VulnType = Literal["injection", "xss", "auth", "ssrf", "authz"]

class AgentName(str, Enum):
    PRE_RECON = "pre-recon"
    RECON = "recon"
    INJECTION_VULN = "injection-vuln"
    XSS_VULN = "xss-vuln"
    AUTH_VULN = "auth-vuln"
    SSRF_VULN = "ssrf-vuln"
    AUTHZ_VULN = "authz-vuln"
    RECON_BLACKBOX = "recon-blackbox"
    INJECTION_EXPLOIT = "injection-exploit"
    XSS_EXPLOIT = "xss-exploit"
    AUTH_EXPLOIT = "auth-exploit"
    SSRF_EXPLOIT = "ssrf-exploit"
    AUTHZ_EXPLOIT = "authz-exploit"
    REPORT = "report"
    VALIDATE_AUTH = "validate-authentication"
    AUDIT_TIER1 = "audit-tier1"

class AgentDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: AgentName
    display_name: str
    prerequisites: list[AgentName]
    prompt_template: str
    deliverable_filename: str | None = None
    model_tier: Literal["small", "medium", "large"] = "medium"

AGENTS: dict[AgentName, AgentDefinition] = {
    AgentName.PRE_RECON: AgentDefinition(
        name=AgentName.PRE_RECON,
        display_name="Pre-recon agent",
        prerequisites=[],
        prompt_template="pre-recon-code",
        deliverable_filename="pre_recon_deliverable.md",
        model_tier="large",
    ),
    AgentName.RECON: AgentDefinition(
        name=AgentName.RECON,
        display_name="Recon agent",
        prerequisites=[AgentName.PRE_RECON],
        prompt_template="recon",
        deliverable_filename="recon_deliverable.md",
    ),
    AgentName.INJECTION_VULN: AgentDefinition(
        name=AgentName.INJECTION_VULN,
        display_name="Injection vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-injection",
        deliverable_filename="injection_analysis_deliverable.md",
    ),
    AgentName.XSS_VULN: AgentDefinition(
        name=AgentName.XSS_VULN,
        display_name="XSS vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-xss",
        deliverable_filename="xss_analysis_deliverable.md",
    ),
    AgentName.AUTH_VULN: AgentDefinition(
        name=AgentName.AUTH_VULN,
        display_name="Auth vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-auth",
        deliverable_filename="auth_analysis_deliverable.md",
    ),
    AgentName.SSRF_VULN: AgentDefinition(
        name=AgentName.SSRF_VULN,
        display_name="SSRF vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-ssrf",
        deliverable_filename="ssrf_analysis_deliverable.md",
    ),
    AgentName.AUTHZ_VULN: AgentDefinition(
        name=AgentName.AUTHZ_VULN,
        display_name="Authz vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-authz",
        deliverable_filename="authz_analysis_deliverable.md",
    ),
    AgentName.RECON_BLACKBOX: AgentDefinition(
        name=AgentName.RECON_BLACKBOX,
        display_name="Reconnaissance (Black-Box)",
        prerequisites=[],
        prompt_template="recon-blackbox",
        deliverable_filename="recon_deliverable.md",
    ),
    AgentName.INJECTION_EXPLOIT: AgentDefinition(
        name=AgentName.INJECTION_EXPLOIT,
        display_name="Injection Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="injection-exploit",
        deliverable_filename="injection_exploitation_evidence.md",
    ),
    AgentName.XSS_EXPLOIT: AgentDefinition(
        name=AgentName.XSS_EXPLOIT,
        display_name="XSS Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="xss-exploit",
        deliverable_filename="xss_exploitation_evidence.md",
    ),
    AgentName.AUTH_EXPLOIT: AgentDefinition(
        name=AgentName.AUTH_EXPLOIT,
        display_name="Auth Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="auth-exploit",
        deliverable_filename="auth_exploitation_evidence.md",
    ),
    AgentName.SSRF_EXPLOIT: AgentDefinition(
        name=AgentName.SSRF_EXPLOIT,
        display_name="SSRF Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="ssrf-exploit",
        deliverable_filename="ssrf_exploitation_evidence.md",
    ),
    AgentName.AUTHZ_EXPLOIT: AgentDefinition(
        name=AgentName.AUTHZ_EXPLOIT,
        display_name="Authz Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="authz-exploit",
        deliverable_filename="authz_exploitation_evidence.md",
    ),
    AgentName.REPORT: AgentDefinition(
        name=AgentName.REPORT,
        display_name="Report Generator",
        prerequisites=[AgentName.INJECTION_EXPLOIT, AgentName.XSS_EXPLOIT,
                        AgentName.AUTH_EXPLOIT, AgentName.SSRF_EXPLOIT,
                        AgentName.AUTHZ_EXPLOIT],
        prompt_template="report-executive",
        deliverable_filename="comprehensive_security_assessment_report.md",
    ),
    AgentName.VALIDATE_AUTH: AgentDefinition(
        name=AgentName.VALIDATE_AUTH,
        display_name="Authentication Validation",
        prerequisites=[],
        prompt_template="validate-authentication",
        deliverable_filename=None,
        model_tier="medium",
    ),
    AgentName.AUDIT_TIER1: AgentDefinition(
        name=AgentName.AUDIT_TIER1,
        display_name="Tier 1 Combined Audit",
        prerequisites=[AgentName.RECON],
        prompt_template="audit-tier1",
        deliverable_filename=None,  # Findings collected, no separate deliverable
        model_tier="small",
    ),
}

ALL_VULN_CLASSES: list[VulnType] = ["injection", "xss", "auth", "ssrf", "authz"]

PLAYWRIGHT_SESSION_MAPPING: dict[str, str] = {name.value: f"agent{i}" for i, name in enumerate(AgentName, 1)}
# VALIDATE_AUTH shares agent1 slot (same browser session as preflight)
PLAYWRIGHT_SESSION_MAPPING[AgentName.VALIDATE_AUTH.value] = "agent1"
PLAYWRIGHT_SESSION_MAPPING[AgentName.AUDIT_TIER1.value] = f"agent{len(AgentName)}"

AGENT_PHASE_MAP: dict[str, str] = {
    "pre-recon": "pre-recon",
    "recon": "recon",
    "injection-vuln": "vulnerability-analysis",
    "xss-vuln": "vulnerability-analysis",
    "auth-vuln": "vulnerability-analysis",
    "ssrf-vuln": "vulnerability-analysis",
    "authz-vuln": "vulnerability-analysis",
    "recon-blackbox": "recon",
    "injection-exploit": "exploitation",
    "xss-exploit": "exploitation",
    "auth-exploit": "exploitation",
    "ssrf-exploit": "exploitation",
    "authz-exploit": "exploitation",
    "report": "reporting",
    "validate-authentication": "pre-recon",
    "audit-tier1": "vulnerability-analysis",
}
