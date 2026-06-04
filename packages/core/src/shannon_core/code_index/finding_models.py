"""VulnFinding model, category whitelists, validation, and deduplication.

Provides:
- VulnFinding: Structured vulnerability finding from audit agents
- Category/issue_type whitelists for each vulnerability class
- parse_and_validate_findings(): Filter raw LLM output against whitelists
- deduplicate_findings(): 5-tuple deduplication
"""

import logging
from enum import Enum
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


class FindingVerdict(str, Enum):
    """Verdict for a vulnerability finding."""
    VULNERABLE = "vulnerable"
    NOT_VULNERABLE = "not_vulnerable"
    NEEDS_REVIEW = "needs_review"


# ── Category/Issue-Type Whitelists ───────────────────────────
# These define the valid structured output categories for each agent type.
# Any finding not matching these is discarded.

VALID_INJECTION_CATEGORIES: set[str] = {
    "sql_injection", "command_injection", "path_traversal",
    "ssti", "ldap_injection", "nosql_injection",
}

VALID_AUTH_CATEGORIES: set[str] = {
    "broken_authentication", "weak_password_policy",
    "credential_stuffing", "session_fixation",
    "insecure_password_storage", "missing_brute_force_protection",
}

VALID_XSS_CATEGORIES: set[str] = {
    "reflected_xss", "stored_xss", "dom_xss",
}

VALID_AUTHZ_CATEGORIES: set[str] = {
    "idor", "missing_role_check", "role_tampering",
    "tenant_isolation", "cross_org_access",
    "horizontal_privilege_escalation", "vertical_privilege_escalation",
}

VALID_SSRF_CATEGORIES: set[str] = {
    "server_side_request_forgery", "dns_rebinding",
    "internal_service_access",
}

VALID_MISCONFIG_CATEGORIES: set[str] = {
    "cors_misconfiguration", "missing_security_headers",
    "insecure_cookie_flags", "information_disclosure",
    "debug_mode_enabled", "default_credentials",
}

# Map agent_type → valid issue_types
AGENT_TYPE_WHITELIST: dict[str, set[str]] = {
    "injection": VALID_INJECTION_CATEGORIES,
    "auth": VALID_AUTH_CATEGORIES,
    "xss": VALID_XSS_CATEGORIES,
    "authz": VALID_AUTHZ_CATEGORIES,
    "ssrf": VALID_SSRF_CATEGORIES,
    "misconfig": VALID_MISCONFIG_CATEGORIES,
}


class VulnFinding(BaseModel):
    """A structured vulnerability finding from an audit agent."""
    category: str                           # "injection" | "auth" | "xss" | ...
    issue_type: str                         # "sql_injection" | "idor" | ...
    entry_point_id: str                     # FuncBlock.id of the entry point
    vulnerable_function_id: str             # FuncBlock.id of the vulnerable function
    call_chain_path: list[str]              # Function names in the chain
    confidence: float                       # 0.0-1.0
    verdict: FindingVerdict = FindingVerdict.VULNERABLE
    title: str = ""
    description: str = ""
    code_location: str = ""                 # "file:line"
    remediation: str = ""
    evidence: str = ""

    @property
    def dedup_key(self) -> tuple:
        """5-tuple deduplication key."""
        return (
            self.entry_point_id,
            self.category,
            self.issue_type,
            self.vulnerable_function_id,
            tuple(self.call_chain_path),
        )


def parse_and_validate_findings(
    raw: dict,
    agent_type: str,
) -> list[VulnFinding]:
    """Parse raw LLM JSON output and validate against whitelists.

    Args:
        raw: Parsed JSON dict with "findings" key containing a list.
        agent_type: "injection" | "auth" | "xss" | "authz" | "ssrf" | "misconfig"

    Returns:
        List of validated VulnFinding objects. Invalid entries are silently dropped.
    """
    valid_issues = AGENT_TYPE_WHITELIST.get(agent_type, set())
    if not valid_issues:
        logger.warning("Unknown agent type: %s, no whitelist", agent_type)
        return []

    raw_findings = raw.get("findings", [])
    results: list[VulnFinding] = []

    for item in raw_findings:
        # Validate issue_type against whitelist
        issue_type = item.get("issue_type", "")
        category = item.get("category", "")

        if issue_type not in valid_issues:
            logger.debug("Filtered finding: issue_type=%s not in %s whitelist",
                         issue_type, agent_type)
            continue

        # Try to parse as VulnFinding
        try:
            finding = VulnFinding(**item)
            results.append(finding)
        except (ValidationError, TypeError) as exc:
            logger.warning("Invalid finding structure: %s", exc)
            continue

    return results


def deduplicate_findings(findings: list[VulnFinding]) -> list[VulnFinding]:
    """Deduplicate findings using 5-tuple key.

    Dedup key: (entry_point_id, category, issue_type,
                vulnerable_function_id, tuple(call_chain_path))

    Keeps the first occurrence when duplicates are found.
    """
    seen: set[tuple] = set()
    unique: list[VulnFinding] = []

    for finding in findings:
        key = finding.dedup_key
        if key not in seen:
            seen.add(key)
            unique.append(finding)

    return unique
