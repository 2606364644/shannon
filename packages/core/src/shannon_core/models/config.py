from typing import Literal

from pydantic import BaseModel

RuleType = Literal["url_path", "subdomain", "domain", "method", "header", "parameter", "code_path"]

class Rule(BaseModel):
    description: str
    type: RuleType
    value: str

class Rules(BaseModel):
    avoid: list[Rule] = []
    focus: list[Rule] = []

VulnClass = Literal["injection", "xss", "auth", "authz", "ssrf", "misconfig"]
Severity = Literal["low", "medium", "high", "critical"]
Confidence = Literal["low", "medium", "high"]

class ReportConfig(BaseModel):
    min_severity: Severity | None = None
    min_confidence: Confidence | None = None
    guidance: str | None = None

class SuccessCondition(BaseModel):
    type: Literal["url_contains", "element_present", "url_equals_exactly", "text_contains"]
    value: str

class EmailLogin(BaseModel):
    address: str
    password: str
    totp_secret: str | None = None

class Credentials(BaseModel):
    username: str
    password: str | None = None
    totp_secret: str | None = None
    email_login: EmailLogin | None = None

class Authentication(BaseModel):
    login_type: Literal["form", "sso", "api", "basic"]
    login_url: str
    credentials: Credentials
    login_flow: list[str] | None = None
    success_condition: SuccessCondition

class PipelineConfig(BaseModel):
    retry_preset: Literal["default", "subscription"] | None = None
    max_concurrent_pipelines: int | None = None

class DistributedConfig(BaseModel):
    avoid: list[Rule]
    focus: list[Rule]
    description: str
    vuln_classes: list[VulnClass]
    exploit: bool
    report: ReportConfig
    rules_of_engagement: str
    authentication: Authentication | None = None

class Config(BaseModel):
    rules: Rules | None = None
    authentication: Authentication | None = None
    pipeline: PipelineConfig | None = None
    description: str | None = None
    vuln_classes: list[VulnClass] | None = None
    exploit: bool = True
    report: ReportConfig | None = None
    rules_of_engagement: str | None = None

ALL_VULN_CLASSES: list[VulnClass] = ["injection", "xss", "auth", "authz", "ssrf", "misconfig"]
