import pytest
from shannon_core.code_index.finding_models import (
    VulnFinding, FindingVerdict,
    parse_and_validate_findings, deduplicate_findings,
    VALID_INJECTION_CATEGORIES, VALID_AUTH_CATEGORIES,
    VALID_XSS_CATEGORIES, VALID_AUTHZ_CATEGORIES,
    VALID_SSRF_CATEGORIES, VALID_MISCONFIG_CATEGORIES,
)


def _finding(
    category: str = "injection",
    issue_type: str = "sql_injection",
    entry_point_id: str = "app.py:handler:1",
    vulnerable_func_id: str = "svc.py:query:10",
    chain_path: tuple = ("handler", "query"),
    confidence: float = 0.9,
) -> VulnFinding:
    return VulnFinding(
        category=category,
        issue_type=issue_type,
        entry_point_id=entry_point_id,
        vulnerable_function_id=vulnerable_func_id,
        call_chain_path=list(chain_path),
        confidence=confidence,
        verdict=FindingVerdict.VULNERABLE,
        title=f"{category}: {issue_type}",
        description="Test finding",
        code_location="svc.py:10",
    )


class TestVulnFinding:
    def test_basic_creation(self):
        f = _finding()
        assert f.category == "injection"
        assert f.confidence == 0.9
        assert f.verdict == FindingVerdict.VULNERABLE

    def test_dedup_key(self):
        f = _finding()
        key = f.dedup_key
        assert "app.py:handler:1" in key
        assert "sql_injection" in key


class TestCategoryWhitelists:
    def test_injection_categories(self):
        assert "sql_injection" in VALID_INJECTION_CATEGORIES
        assert "command_injection" in VALID_INJECTION_CATEGORIES
        assert "path_traversal" in VALID_INJECTION_CATEGORIES
        assert "ssti" in VALID_INJECTION_CATEGORIES

    def test_auth_categories(self):
        assert "broken_authentication" in VALID_AUTH_CATEGORIES
        assert "credential_stuffing" in VALID_AUTH_CATEGORIES

    def test_xss_categories(self):
        assert "reflected_xss" in VALID_XSS_CATEGORIES
        assert "stored_xss" in VALID_XSS_CATEGORIES
        assert "dom_xss" in VALID_XSS_CATEGORIES

    def test_authz_categories(self):
        assert "idor" in VALID_AUTHZ_CATEGORIES
        assert "missing_role_check" in VALID_AUTHZ_CATEGORIES

    def test_ssrf_categories(self):
        assert "server_side_request_forgery" in VALID_SSRF_CATEGORIES

    def test_misconfig_categories(self):
        assert "cors_misconfiguration" in VALID_MISCONFIG_CATEGORIES


class TestParseAndValidateFindings:
    def test_valid_findings(self):
        raw = {
            "findings": [
                {
                    "category": "injection",
                    "issue_type": "sql_injection",
                    "entry_point_id": "a.py:f:1",
                    "vulnerable_function_id": "b.py:g:2",
                    "call_chain_path": ["f", "g"],
                    "confidence": 0.9,
                    "title": "SQL Injection",
                    "description": "Unsanitized input",
                    "code_location": "b.py:2",
                },
            ]
        }
        results = parse_and_validate_findings(raw, "injection")
        assert len(results) == 1
        assert results[0].issue_type == "sql_injection"

    def test_invalid_category_filtered(self):
        raw = {
            "findings": [
                {
                    "category": "injection",
                    "issue_type": "fake_vulnerability",
                    "entry_point_id": "a:f:1",
                    "vulnerable_function_id": "b:g:2",
                    "call_chain_path": ["f", "g"],
                    "confidence": 0.9,
                    "title": "Fake",
                    "description": "Not real",
                    "code_location": "b.py:2",
                },
            ]
        }
        results = parse_and_validate_findings(raw, "injection")
        assert len(results) == 0  # Filtered out

    def test_wrong_agent_type_filtered(self):
        raw = {
            "findings": [
                {
                    "category": "xss",
                    "issue_type": "reflected_xss",
                    "entry_point_id": "a:f:1",
                    "vulnerable_function_id": "b:g:2",
                    "call_chain_path": ["f", "g"],
                    "confidence": 0.9,
                    "title": "XSS",
                    "description": "Found XSS",
                    "code_location": "b.py:2",
                },
            ]
        }
        results = parse_and_validate_findings(raw, "injection")
        assert len(results) == 0  # XSS not valid for injection agent

    def test_empty_findings(self):
        results = parse_and_validate_findings({"findings": []}, "injection")
        assert results == []

    def test_malformed_entry_skipped(self):
        raw = {
            "findings": [
                {"category": "injection"},  # Missing required fields
            ]
        }
        results = parse_and_validate_findings(raw, "injection")
        assert len(results) == 0


class TestDeduplicateFindings:
    def test_removes_exact_duplicates(self):
        findings = [_finding(), _finding()]
        result = deduplicate_findings(findings)
        assert len(result) == 1

    def test_keeps_different_chain_paths(self):
        f1 = _finding(chain_path=("handler", "query_a"))
        f2 = _finding(chain_path=("handler", "query_b"))
        result = deduplicate_findings([f1, f2])
        assert len(result) == 2

    def test_keeps_different_categories(self):
        f1 = _finding(category="injection", issue_type="sql_injection")
        f2 = _finding(category="injection", issue_type="command_injection",
                      vulnerable_func_id="svc.py:exec:20")
        result = deduplicate_findings([f1, f2])
        assert len(result) == 2

    def test_keeps_different_entry_points(self):
        f1 = _finding(entry_point_id="a.py:f:1")
        f2 = _finding(entry_point_id="a.py:g:5",
                      vulnerable_func_id="svc.py:query:10")
        result = deduplicate_findings([f1, f2])
        assert len(result) == 2

    def test_empty_list(self):
        result = deduplicate_findings([])
        assert result == []

    def test_five_tuple_dedup(self):
        """Dedup key = (entry_point, category, issue_type, vuln_func, chain_path)."""
        f1 = _finding(
            category="injection", issue_type="sql_injection",
            entry_point_id="a.py:f:1", vulnerable_func_id="b.py:g:2",
            chain_path=("f", "g"),
        )
        f2 = _finding(
            category="injection", issue_type="sql_injection",
            entry_point_id="a.py:f:1", vulnerable_func_id="b.py:g:2",
            chain_path=("f", "g"),
            confidence=0.95,  # Different confidence, same 5-tuple
        )
        result = deduplicate_findings([f1, f2])
        assert len(result) == 1
        assert result[0].confidence == 0.9  # Keeps first occurrence
