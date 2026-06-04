"""Tests for multi-level queue validation."""
import json
import pytest

from shannon_blackbox.services.exploitation_checker import ExploitationChecker, QueueValidationResult


class TestValidateQueue:
    @pytest.mark.asyncio
    async def test_valid_queue_with_deliverable(self, tmp_path):
        queue_data = {"vulnerabilities": [
            {"ID": "INJ-001", "vulnerability_type": "SQL Injection",
             "externally_exploitable": True, "confidence": "high"},
        ]}
        (tmp_path / "injection_exploitation_queue.json").write_text(json.dumps(queue_data))
        (tmp_path / "injection_analysis_deliverable.md").write_text("# Analysis")
        result = await ExploitationChecker.validate_queue("injection", tmp_path)
        assert result.valid is True
        assert result.vuln_count == 1
        assert result.reason == ""

    @pytest.mark.asyncio
    async def test_queue_file_missing(self, tmp_path):
        result = await ExploitationChecker.validate_queue("injection", tmp_path)
        assert result.valid is False
        assert result.reason == "queue_file_missing"
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_queue_invalid_json(self, tmp_path):
        (tmp_path / "xss_exploitation_queue.json").write_text("not json {{{")
        result = await ExploitationChecker.validate_queue("xss", tmp_path)
        assert result.valid is False
        assert result.reason == "json_parse_error"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_queue_missing_vulnerabilities_key(self, tmp_path):
        (tmp_path / "auth_exploitation_queue.json").write_text(json.dumps({"data": "x"}))
        result = await ExploitationChecker.validate_queue("auth", tmp_path)
        assert result.valid is False
        assert result.reason == "invalid_vulnerabilities_array"

    @pytest.mark.asyncio
    async def test_queue_vulnerabilities_not_list(self, tmp_path):
        (tmp_path / "ssrf_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": "not a list"})
        )
        result = await ExploitationChecker.validate_queue("ssrf", tmp_path)
        assert result.valid is False
        assert result.reason == "invalid_vulnerabilities_array"

    @pytest.mark.asyncio
    async def test_queue_missing_deliverable(self, tmp_path):
        queue_data = {"vulnerabilities": [
            {"ID": "INJ-001", "vulnerability_type": "SQL Injection",
             "externally_exploitable": True, "confidence": "high"},
        ]}
        (tmp_path / "injection_exploitation_queue.json").write_text(json.dumps(queue_data))
        # No deliverable file created
        result = await ExploitationChecker.validate_queue("injection", tmp_path)
        assert result.valid is False
        assert result.reason == "deliverable_missing"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_queue_empty_vulnerabilities(self, tmp_path):
        queue_data = {"vulnerabilities": []}
        (tmp_path / "authz_exploitation_queue.json").write_text(json.dumps(queue_data))
        (tmp_path / "authz_analysis_deliverable.md").write_text("# Analysis")
        result = await ExploitationChecker.validate_queue("authz", tmp_path)
        assert result.valid is False
        assert result.reason == "empty_vulnerabilities"

    @pytest.mark.asyncio
    async def test_should_exploit_returns_bool(self, tmp_path):
        """Backward compatibility: should_exploit still returns bool."""
        queue_data = {"vulnerabilities": [
            {"ID": "INJ-001", "vulnerability_type": "SQL Injection",
             "externally_exploitable": True, "confidence": "high"},
        ]}
        (tmp_path / "injection_exploitation_queue.json").write_text(json.dumps(queue_data))
        (tmp_path / "injection_analysis_deliverable.md").write_text("# Analysis")
        result = await ExploitationChecker.should_exploit(
            deliverables_path=tmp_path, vuln_type="injection"
        )
        assert isinstance(result, bool)
        assert result is True

    @pytest.mark.asyncio
    async def test_should_exploit_disabled(self, tmp_path):
        result = await ExploitationChecker.should_exploit(
            deliverables_path=tmp_path, vuln_type="injection", exploit_enabled=False
        )
        assert result is False
