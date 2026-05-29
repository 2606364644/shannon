from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict

VulnType = Literal["injection", "xss", "auth", "ssrf", "authz", "misconfig"]

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
    MISCONFIG_VULN = "misconfig-vuln"
    MISCONFIG_EXPLOIT = "misconfig-exploit"
    REPORT = "report"

class AgentDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: AgentName
    display_name: str
    prerequisites: list[AgentName]
    prompt_template: str
    deliverable_filename: str
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
    AgentName.MISCONFIG_VULN: AgentDefinition(
        name=AgentName.MISCONFIG_VULN,
        display_name="Misconfig Vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-misconfig",
        deliverable_filename="misconfig_analysis_deliverable.md",
    ),
    AgentName.MISCONFIG_EXPLOIT: AgentDefinition(
        name=AgentName.MISCONFIG_EXPLOIT,
        display_name="Misconfig Exploitation",
        prerequisites=[AgentName.MISCONFIG_VULN],
        prompt_template="misconfig-exploit",
        deliverable_filename="misconfig_exploitation_evidence.md",
    ),
    AgentName.REPORT: AgentDefinition(
        name=AgentName.REPORT,
        display_name="Report Generator",
        prerequisites=[AgentName.INJECTION_EXPLOIT, AgentName.XSS_EXPLOIT,
                        AgentName.AUTH_EXPLOIT, AgentName.SSRF_EXPLOIT,
                        AgentName.AUTHZ_EXPLOIT, AgentName.MISCONFIG_EXPLOIT],
        prompt_template="report-executive",
        deliverable_filename="comprehensive_security_assessment_report.md",
    ),
}

ALL_VULN_CLASSES: list[VulnType] = ["injection", "xss", "auth", "ssrf", "authz", "misconfig"]

PLAYWRIGHT_SESSION_MAPPING: dict[str, str] = {name.value: f"agent{i}" for i, name in enumerate(AgentName, 1)}
