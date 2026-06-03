import json

import pytest

from shannon_core.models.config import ReportConfig
from shannon_core.models.queue_schemas import (
    InjectionVulnerability,
    XssVulnerability,
    AuthVulnerability,
    SsrfVulnerability,
    AuthzVulnerability,
    MisconfigVulnerability,
    VulnerabilityQueue,
)
from shannon_core.services.findings_renderer import (
    render_injection_entry,
    render_xss_entry,
    render_auth_entry,
    render_authz_entry,
    render_ssrf_entry,
    render_misconfig_entry,
    filter_vulnerabilities,
    FindingsRenderer,
)


def test_render_injection_entry_full():
    vuln = InjectionVulnerability(
        ID="INJECTION-VULN-001",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="high",
        source="user input",
        path="/api/users",
        sink_call="sqlite3.execute",
        concat_occurrences="query + user_input",
        sanitization_observed="None",
        verdict="Exploitable",
        witness_payload="' OR 1=1 --",
        notes="Critical finding",
    )
    result = render_injection_entry(vuln)
    assert "### INJECTION-VULN-001" in result
    assert "**Vulnerable Location:** user input → /api/users" in result
    assert "**Sink Call:** sqlite3.execute" in result
    assert "**Concat Occurrences:** query + user_input" in result
    assert "**Sanitization Observed:** None" in result
    assert "**Verdict:** Exploitable" in result
    assert "**Witness Payload:** ' OR 1=1 --" in result
    assert "**Notes:** Critical finding" in result


def test_render_injection_entry_minimal():
    vuln = InjectionVulnerability(
        ID="INJECTION-VULN-002",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="medium",
    )
    result = render_injection_entry(vuln)
    assert "### INJECTION-VULN-002" in result
    assert "Sink Call" not in result
    assert "Notes" not in result


def test_render_xss_entry_full():
    vuln = XssVulnerability(
        ID="XSS-VULN-001",
        vulnerability_type="Reflected XSS",
        externally_exploitable=True,
        confidence="high",
        source="query param",
        path="/search",
        sink_function="innerHTML",
        render_context="HTML context",
        encoding_observed="None",
        verdict="Exploitable",
        witness_payload="<script>alert(1)</script>",
    )
    result = render_xss_entry(vuln)
    assert "### XSS-VULN-001" in result
    assert "**Vulnerable Location:** query param → /search" in result
    assert "**Sink Function:** innerHTML" in result
    assert "**Render Context:** HTML context" in result
    assert "**Encoding Observed:** None" in result
    assert "**Verdict:** Exploitable" in result
    assert "**Witness Payload:** <script>alert(1)</script>" in result


def test_render_auth_entry_full():
    vuln = AuthVulnerability(
        ID="AUTH-VULN-001",
        vulnerability_type="Broken Authentication",
        externally_exploitable=True,
        confidence="high",
        source_endpoint="/api/login",
        vulnerable_code_location="auth/handlers.py:42",
        missing_defense="No rate limiting",
        exploitation_hypothesis="Brute force possible",
        suggested_exploit_technique="Dictionary attack",
    )
    result = render_auth_entry(vuln)
    assert "### AUTH-VULN-001" in result
    assert "**Source Endpoint:** /api/login" in result
    assert "**Vulnerable Code Location:** auth/handlers.py:42" in result
    assert "**Missing Defense:** No rate limiting" in result
    assert "**Exploitation Hypothesis:** Brute force possible" in result
    assert "**Suggested Exploit Technique:** Dictionary attack" in result


def test_render_authz_entry_full():
    vuln = AuthzVulnerability(
        ID="AUTHZ-VULN-001",
        vulnerability_type="IDOR",
        externally_exploitable=True,
        confidence="high",
        endpoint="/api/users/{id}",
        vulnerable_code_location="api/users.py:15",
        role_context="Authenticated user",
        guard_evidence="No ownership check",
        side_effect="Access other users' data",
        reason="Missing authorization middleware",
        minimal_witness="GET /api/users/1234 → 200 OK",
    )
    result = render_authz_entry(vuln)
    assert "### AUTHZ-VULN-001" in result
    assert "**Endpoint:** /api/users/{id}" in result
    assert "**Role Context:** Authenticated user" in result
    assert "**Guard Evidence:** No ownership check" in result
    assert "**Side Effect:** Access other users' data" in result
    assert "**Reason:** Missing authorization middleware" in result
    assert "**Minimal Witness:** GET /api/users/1234 → 200 OK" in result


def test_render_ssrf_entry_full():
    vuln = SsrfVulnerability(
        ID="SSRF-VULN-001",
        vulnerability_type="SSRF",
        externally_exploitable=True,
        confidence="high",
        source_endpoint="/api/fetch",
        vulnerable_parameter="url",
        vulnerable_code_location="api/fetch.py:20",
        missing_defense="No URL allowlist",
        exploitation_hypothesis="Internal network scan",
        suggested_exploit_technique="URL manipulation",
    )
    result = render_ssrf_entry(vuln)
    assert "### SSRF-VULN-001" in result
    assert "**Source Endpoint:** /api/fetch" in result
    assert "**Vulnerable Parameter:** url" in result
    assert "**Missing Defense:** No URL allowlist" in result


def test_render_misconfig_entry_full():
    vuln = MisconfigVulnerability(
        ID="MISCONFIG-VULN-001",
        vulnerability_type="Open Redirect",
        externally_exploitable=True,
        confidence="high",
        source_endpoint="/redirect",
        vulnerable_parameter="next",
        vulnerable_code_location="handlers/redirect.py:10",
        missing_defense="No redirect validation",
        exploitation_hypothesis="Phishing via redirect",
        suggested_exploit_technique="Parameter manipulation",
        redirect_sink="/redirect?url=",
        existing_validation="None",
    )
    result = render_misconfig_entry(vuln)
    assert "### MISCONFIG-VULN-001" in result
    assert "**Source Endpoint:** /redirect" in result
    assert "**Redirect Sink:** /redirect?url=" in result
    assert "**Existing Validation:** None" in result


def test_filter_by_confidence():
    vulns = [
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="low",
        ),
        InjectionVulnerability(
            ID="INJECTION-002", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="medium",
        ),
        InjectionVulnerability(
            ID="INJECTION-003", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
        ),
    ]
    queue = VulnerabilityQueue(vulnerabilities=vulns)
    config = ReportConfig(min_confidence="medium")
    result = filter_vulnerabilities(queue, config)
    assert len(result) == 2
    assert all(v.ID != "INJECTION-001" for v in result)


def test_filter_with_no_config():
    vulns = [
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="low",
        ),
    ]
    queue = VulnerabilityQueue(vulnerabilities=vulns)
    config = ReportConfig()
    result = filter_vulnerabilities(queue, config)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_render_findings_from_queues(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="user input", path="/api/search",
            sink_call="db.execute",
        ),
    ])
    (deliverables / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json(indent=2)
    )

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = (deliverables / "injection_findings.md").read_text()
    assert "## Injection Vulnerabilities" in findings
    assert "### INJECTION-001" in findings
    assert "**Sink Call:** db.execute" in findings
    assert "Disclaimer" in findings


@pytest.mark.asyncio
async def test_render_findings_skips_existing_findings(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    (deliverables / "injection_findings.md").write_text("Existing content")
    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
        ),
    ])
    (deliverables / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json()
    )

    await FindingsRenderer.render_findings_from_queues(deliverables)

    content = (deliverables / "injection_findings.md").read_text()
    assert content == "Existing content"


@pytest.mark.asyncio
async def test_render_findings_empty_queue(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    queue = VulnerabilityQueue(vulnerabilities=[])
    (deliverables / "xss_exploitation_queue.json").write_text(
        queue.model_dump_json()
    )

    await FindingsRenderer.render_findings_from_queues(deliverables)

    findings = (deliverables / "xss_findings.md").read_text()
    assert "No XSS vulnerabilities found." in findings


@pytest.mark.asyncio
async def test_render_findings_skips_missing_queue(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    await FindingsRenderer.render_findings_from_queues(deliverables)

    assert not (deliverables / "injection_findings.md").exists()


@pytest.mark.asyncio
async def test_render_findings_with_confidence_filter(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    queue = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(
            ID="INJECTION-001", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="low",
        ),
        InjectionVulnerability(
            ID="INJECTION-002", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
        ),
    ])
    (deliverables / "injection_exploitation_queue.json").write_text(
        queue.model_dump_json()
    )

    config = ReportConfig(min_confidence="high")
    await FindingsRenderer.render_findings_from_queues(deliverables, config)

    findings = (deliverables / "injection_findings.md").read_text()
    assert "INJECTION-002" in findings
    assert "INJECTION-001" not in findings
