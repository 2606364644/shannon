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
}

ALL_VULN_CLASSES: list[VulnType] = ["injection", "xss", "auth", "ssrf", "authz"]
