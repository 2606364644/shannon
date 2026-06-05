"""End-to-end integration tests for whitebox->blackbox handoff.

These tests verify the data contracts and file I/O between whitebox
and blackbox without running the full Temporal workflows.
"""

import json
from pathlib import Path

import pytest

from shannon_core.session import SessionManager
from shannon_core.utils.paths import has_valid_whitebox_results
from shannon_core.utils.atomic_write import atomic_write_json
from shannon_core.workspace import compute_deliverables_summary, find_workspaces_by_url
from shannon_core.services.workspace_discovery import WorkspaceDiscovery


class TestWhiteboxProducesCompleteDeliverables:
    """Whitebox completion yields all expected queue files."""

    def test_deliverables_have_valid_schema(self, tmp_path):
        """Each exploitation queue file should pass schema validation."""
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", "/repo", name="wb-complete")
        mgr.mark_completed(ws)

        deliverables = ws / "deliverables"
        deliverables.mkdir()

        for vc in ["injection", "xss", "auth", "ssrf"]:
            queue_file = deliverables / f"{vc}_exploitation_queue.json"
            atomic_write_json(queue_file, {
                "vulnerabilities": [{
                    "title": f"{vc} vuln",
                    "description": f"A {vc} vulnerability was found",
                    "severity": "high",
                    "location": f"src/{vc}.py:10",
                }]
            })

        # Verify all queue files pass validation
        for vc in ["injection", "xss", "auth", "ssrf"]:
            queue_file = deliverables / f"{vc}_exploitation_queue.json"
            assert has_valid_whitebox_results(queue_file), f"{vc} queue failed validation"

        # Verify deliverables summary
        summary = compute_deliverables_summary(ws)
        assert set(summary["vuln_queues"]) == {"injection", "xss", "auth", "ssrf"}


class TestBlackboxLoadsWhiteboxResults:
    """Blackbox discovers and loads whitebox deliverables."""

    def test_discovery_finds_whitebox_workspace(self, tmp_path):
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", "/repo", name="wb-discover")
        mgr.mark_completed(ws)
        deliverables = ws / "deliverables"
        deliverables.mkdir()
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{
                "title": "SQLi", "description": "d", "severity": "high", "location": "a.py:1"
            }]}),
            encoding="utf-8",
        )

        results = find_workspaces_by_url(tmp_path / "workspaces", "https://myapp.com")
        assert len(results) == 1
        ws_path, summary = results[0]
        assert ws_path.name == "wb-discover"
        assert "injection" in summary["vuln_queues"]

    def test_workspace_discovery_service_finds_workspace(self, tmp_path):
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", "/repo", name="wb-svc")
        mgr.mark_completed(ws)
        deliverables = ws / "deliverables"
        deliverables.mkdir()
        (deliverables / "auth_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{
                "title": "Broken Auth", "description": "d", "severity": "critical", "location": "auth.py:5"
            }]}),
            encoding="utf-8",
        )

        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        result = discovery.find_for_blackbox("https://myapp.com", latest=True)
        assert result.workspace_path is not None
        assert result.workspace_path.name == "wb-svc"


class TestBlackboxFallbackOnEmptyResults:
    """Empty whitebox results -> blackbox runs standalone recon."""

    def test_no_whitebox_results_returns_empty(self, tmp_path):
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", "/repo", name="wb-empty")
        mgr.mark_completed(ws)
        # No deliverables directory

        results = find_workspaces_by_url(tmp_path / "workspaces", "https://myapp.com")
        assert len(results) == 0

    def test_empty_vulns_not_discovered(self, tmp_path):
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", "/repo", name="wb-no-vulns")
        mgr.mark_completed(ws)
        deliverables = ws / "deliverables"
        deliverables.mkdir()
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": []}),
            encoding="utf-8",
        )

        results = find_workspaces_by_url(tmp_path / "workspaces", "https://myapp.com")
        assert len(results) == 0


class TestAtomicWriteSurvivesCrash:
    """Partial write doesn't produce readable deliverable."""

    def test_partial_write_not_readable(self, tmp_path):
        """If a tmp file exists (simulating crash mid-write), target should be absent."""
        target = tmp_path / "deliverables" / "injection_exploitation_queue.json"
        target.parent.mkdir(parents=True)

        # Simulate crash: tmp file exists but target doesn't
        tmp_file = target.with_suffix(".json.tmp")
        tmp_file.write_text('{"vulnerabilities": [{"title": "partial', encoding="utf-8")

        # Target should not exist
        assert not target.exists()
        # has_valid_whitebox_results should return False
        assert has_valid_whitebox_results(target) is False


class TestMultiWorkspaceDiscovery:
    """Multiple workspaces sorted by recency with correct summaries."""

    def test_multiple_workspaces_returned(self, tmp_path):
        import time

        mgr = SessionManager(tmp_path / "workspaces")
        ws1 = mgr.create_workspace("https://myapp.com", "/repo", name="ws-old")
        mgr.mark_completed(ws1)
        d1 = ws1 / "deliverables"
        d1.mkdir()
        (d1 / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [
                {"title": "V1", "description": "d", "severity": "high", "location": "a.py:1"}
            ]}),
            encoding="utf-8",
        )

        time.sleep(0.01)

        ws2 = mgr.create_workspace("https://myapp.com", "/repo", name="ws-new")
        mgr.mark_completed(ws2)
        d2 = ws2 / "deliverables"
        d2.mkdir()
        (d2 / "xss_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [
                {"title": "V2", "description": "d", "severity": "medium", "location": "b.py:2"}
            ]}),
            encoding="utf-8",
        )

        results = find_workspaces_by_url(tmp_path / "workspaces", "https://myapp.com")
        assert len(results) == 2
        names = [r[0].name for r in results]
        assert "ws-old" in names
        assert "ws-new" in names


class TestSchemaValidationRejectsMalformed:
    """Invalid vulnerability entries are rejected during validation."""

    def test_missing_fields_rejected(self, tmp_path):
        queue_file = tmp_path / "malformed_exploitation_queue.json"
        queue_file.write_text(
            json.dumps({"vulnerabilities": [{"title": "Only title"}]}),
            encoding="utf-8",
        )
        assert has_valid_whitebox_results(queue_file) is False

    def test_non_dict_entries_rejected(self, tmp_path):
        queue_file = tmp_path / "bad_exploitation_queue.json"
        queue_file.write_text(
            json.dumps({"vulnerabilities": ["string", 42, None]}),
            encoding="utf-8",
        )
        assert has_valid_whitebox_results(queue_file) is False

    def test_truncated_json_rejected(self, tmp_path):
        queue_file = tmp_path / "truncated_exploitation_queue.json"
        queue_file.write_text('{"vulnerabilities": [{"title":', encoding="utf-8")
        assert has_valid_whitebox_results(queue_file) is False
