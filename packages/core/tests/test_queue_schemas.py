import json
from shannon_core.models.queue_schemas import (
    BaseVulnerability, InjectionVulnerability, XssVulnerability,
    AuthVulnerability, SsrfVulnerability, AuthzVulnerability,
    VulnerabilityQueue,
)

def test_base_vulnerability_required_fields():
    v = BaseVulnerability(
        ID="INJECTION-VULN-001",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="high",
    )
    assert v.ID == "INJECTION-VULN-001"
    assert v.notes is None

def test_injection_vulnerability():
    v = InjectionVulnerability(
        ID="INJECTION-VULN-001",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="high",
        source="user input",
        path="/api/users",
        sink_call="sqlite3.execute",
        mismatch_reason="No parameterized query",
    )
    assert v.sink_call == "sqlite3.execute"

def test_xss_vulnerability():
    v = XssVulnerability(
        ID="XSS-VULN-001",
        vulnerability_type="Reflected XSS",
        externally_exploitable=True,
        confidence="medium",
        sink_function="innerHTML",
        path="/search",
    )
    assert v.sink_function == "innerHTML"

def test_auth_vulnerability():
    v = AuthVulnerability(
        ID="AUTH-VULN-001",
        vulnerability_type="Broken Authentication",
        externally_exploitable=True,
        confidence="high",
        source_endpoint="/api/login",
        missing_defense="No rate limiting",
        exploitation_hypothesis="Brute force possible",
    )
    assert v.missing_defense == "No rate limiting"

def test_ssrf_vulnerability():
    v = SsrfVulnerability(
        ID="SSRF-VULN-001",
        vulnerability_type="SSRF",
        externally_exploitable=True,
        confidence="high",
        vulnerable_parameter="url",
    )
    assert v.vulnerable_parameter == "url"

def test_authz_vulnerability():
    v = AuthzVulnerability(
        ID="AUTHZ-VULN-001",
        vulnerability_type="IDOR",
        externally_exploitable=True,
        confidence="high",
        endpoint="/api/users/{id}",
        guard_evidence="No ownership check",
        side_effect="Access other users' data",
    )
    assert v.guard_evidence == "No ownership check"

def test_vulnerability_queue():
    queue = VulnerabilityQueue(vulnerabilities=[])
    assert len(queue.vulnerabilities) == 0

def test_vulnerability_queue_json_roundtrip():
    v = InjectionVulnerability(
        ID="INJECTION-VULN-001",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="high",
        sink_call="execute",
    )
    queue = VulnerabilityQueue(vulnerabilities=[v])
    json_str = queue.model_dump_json(indent=2)
    parsed = json.loads(json_str)
    assert parsed["vulnerabilities"][0]["ID"] == "INJECTION-VULN-001"
    assert parsed["vulnerabilities"][0]["sink_call"] == "execute"

def test_queue_json_matches_ts_format():
    v = InjectionVulnerability(
        ID="INJECTION-VULN-001",
        vulnerability_type="SQL Injection",
        externally_exploitable=True,
        confidence="high",
        source="query param",
        path="/api/search",
        sink_call="db.execute",
        mismatch_reason="String concatenation in query",
    )
    queue = VulnerabilityQueue(vulnerabilities=[v])
    data = json.loads(queue.model_dump_json())
    entry = data["vulnerabilities"][0]
    assert "ID" in entry
    assert "vulnerability_type" in entry
    assert "externally_exploitable" in entry
    assert "confidence" in entry
    assert "source" in entry
    assert "path" in entry
    assert "sink_call" in entry
    assert "mismatch_reason" in entry

def test_misconfig_vulnerability():
    from shannon_core.models.queue_schemas import MisconfigVulnerability
    v = MisconfigVulnerability(
        ID="MISCONFIG-VULN-001",
        vulnerability_type="Missing Security Headers",
        externally_exploitable=True,
        confidence="high",
        missing_defense="No Content-Security-Policy header",
        redirect_sink="/redirect?url=",
    )
    assert v.missing_defense == "No Content-Security-Policy header"
    assert v.redirect_sink == "/redirect?url="

def test_misconfig_in_vulnerability_union():
    from shannon_core.models.queue_schemas import MisconfigVulnerability, VulnerabilityQueue
    v = MisconfigVulnerability(
        ID="MISCONFIG-VULN-001",
        vulnerability_type="Open Redirect",
        externally_exploitable=True,
        confidence="high",
    )
    queue = VulnerabilityQueue(vulnerabilities=[v])
    assert len(queue.vulnerabilities) == 1
    json_str = queue.model_dump_json()
    assert "MISCONFIG-VULN-001" in json_str
