from typing import Union

from pydantic import BaseModel

class BaseVulnerability(BaseModel):
    ID: str
    vulnerability_type: str
    externally_exploitable: bool
    confidence: str
    notes: str | None = None

class InjectionVulnerability(BaseVulnerability):
    source: str | None = None
    combined_sources: str | None = None
    path: str | None = None
    sink_call: str | None = None
    slot_type: str | None = None
    sanitization_observed: str | None = None
    concat_occurrences: str | None = None
    verdict: str | None = None
    mismatch_reason: str | None = None
    witness_payload: str | None = None

class XssVulnerability(BaseVulnerability):
    source: str | None = None
    source_detail: str | None = None
    path: str | None = None
    sink_function: str | None = None
    render_context: str | None = None
    encoding_observed: str | None = None
    verdict: str | None = None
    mismatch_reason: str | None = None
    witness_payload: str | None = None

class AuthVulnerability(BaseVulnerability):
    source_endpoint: str | None = None
    vulnerable_code_location: str | None = None
    missing_defense: str | None = None
    exploitation_hypothesis: str | None = None
    suggested_exploit_technique: str | None = None

class SsrfVulnerability(BaseVulnerability):
    source_endpoint: str | None = None
    vulnerable_parameter: str | None = None
    vulnerable_code_location: str | None = None
    missing_defense: str | None = None
    exploitation_hypothesis: str | None = None
    suggested_exploit_technique: str | None = None

class AuthzVulnerability(BaseVulnerability):
    endpoint: str | None = None
    vulnerable_code_location: str | None = None
    role_context: str | None = None
    guard_evidence: str | None = None
    side_effect: str | None = None
    reason: str | None = None
    minimal_witness: str | None = None

class MisconfigVulnerability(BaseVulnerability):
    source_endpoint: str | None = None
    vulnerable_code_location: str | None = None
    missing_defense: str | None = None
    exploitation_hypothesis: str | None = None
    suggested_exploit_technique: str | None = None
    vulnerable_parameter: str | None = None
    redirect_sink: str | None = None
    existing_validation: str | None = None

Vulnerability = Union[InjectionVulnerability, XssVulnerability, AuthVulnerability, SsrfVulnerability, AuthzVulnerability, MisconfigVulnerability, BaseVulnerability]

class VulnerabilityQueue(BaseModel):
    vulnerabilities: list[Vulnerability] = []
