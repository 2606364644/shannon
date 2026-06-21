from shannon_core.models.queue_schemas import VulnerabilityQueue
from shannon_blackbox.services.exploitation_checker import CoverageResult
from shannon_blackbox.services.coverage_renderer import render_unverified_section


def _queue(*vulns) -> VulnerabilityQueue:
    return VulnerabilityQueue(vulnerabilities=list(vulns))


def test_render_unverified_section_auth_fields():
    from shannon_core.models.queue_schemas import AuthVulnerability
    queue = _queue(AuthVulnerability(
        ID="AUTH-VULN-08", vulnerability_type="Transport_Exposure",
        externally_exploitable=True, confidence="high",
        source_endpoint="ALL auth responses",
        missing_defense="No Cache-Control: no-store",
        suggested_exploit_technique="credential_session_theft",
    ))
    result = CoverageResult(
        vuln_class="auth", total=1,
        covered_ids=frozenset(), uncovered_ids=frozenset({"AUTH-VULN-08"}),
    )
    section = render_unverified_section(result, queue)
    assert "## Unverified Findings (Not Dynamically Exploited)" in section
    assert "### AUTH-VULN-08" in section
    assert "No Cache-Control: no-store" in section
    assert "credential_session_theft" in section
    assert "ALL auth responses" in section


def test_render_unverified_section_authz_fields():
    from shannon_core.models.queue_schemas import AuthzVulnerability
    queue = _queue(AuthzVulnerability(
        ID="AUTHZ-VULN-09", vulnerability_type="IDOR",
        externally_exploitable=True, confidence="high",
        endpoint="GET /api/Users/:id", guard_evidence="no ownership check",
    ))
    result = CoverageResult(
        vuln_class="authz", total=1,
        covered_ids=frozenset(), uncovered_ids=frozenset({"AUTHZ-VULN-09"}),
    )
    section = render_unverified_section(result, queue)
    assert "### AUTHZ-VULN-09" in section
    assert "GET /api/Users/:id" in section
    assert "no ownership check" in section


def test_render_unverified_section_injection_fields():
    from shannon_core.models.queue_schemas import InjectionVulnerability
    queue = _queue(InjectionVulnerability(
        ID="INJECTION-VULN-12", vulnerability_type="SQLi",
        externally_exploitable=True, confidence="medium",
        source="req.body.q", path="/search", verdict="vulnerable",
    ))
    result = CoverageResult(
        vuln_class="injection", total=1,
        covered_ids=frozenset(), uncovered_ids=frozenset({"INJECTION-VULN-12"}),
    )
    section = render_unverified_section(result, queue)
    assert "### INJECTION-VULN-12" in section
    assert "req.body.q" in section
    assert "/search" in section
    assert "vulnerable" in section


def test_render_unverified_section_sorted_and_header_count():
    from shannon_core.models.queue_schemas import AuthVulnerability
    queue = _queue(
        AuthVulnerability(ID="AUTH-VULN-24", vulnerability_type="t",
                          externally_exploitable=True, confidence="high"),
        AuthVulnerability(ID="AUTH-VULN-08", vulnerability_type="t",
                          externally_exploitable=True, confidence="high"),
    )
    result = CoverageResult(
        vuln_class="auth", total=2,
        covered_ids=frozenset(), uncovered_ids=frozenset({"AUTH-VULN-24", "AUTH-VULN-08"}),
    )
    section = render_unverified_section(result, queue)
    # 排序：08 在 24 前
    assert section.index("AUTH-VULN-08") < section.index("AUTH-VULN-24")
    assert "2 条漏洞" in section
