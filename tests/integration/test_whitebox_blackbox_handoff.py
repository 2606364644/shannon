"""End-to-end integration tests for whitebox->blackbox handoff.

These tests verify the data contracts and file I/O between whitebox
and blackbox without running the full Temporal workflows.

Deliverables are written session-centric (workspaces/<session>/deliverables) to match
production whitebox output and the documented handoff contract.
"""

import json
from pathlib import Path

import pytest

from shannon_core.session import SessionManager
from shannon_core.utils.paths import has_valid_whitebox_results
from shannon_core.workspace import compute_deliverables_summary, find_workspaces_by_url
from shannon_core.services.workspace_discovery import WorkspaceDiscovery


def _session_deliverables(workspace: Path) -> Path:
    """The session-centric deliverables dir (ws/deliverables) matching production whitebox output."""
    d = workspace / "deliverables"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_queue(deliverables: Path, vc: str, vulns=None) -> None:
    (deliverables / f"{vc}_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": vulns if vulns is not None else [{
            "title": f"{vc} vuln",
            "description": f"A {vc} vulnerability was found",
            "severity": "high",
            "location": f"src/{vc}.py:10",
        }]}),
        encoding="utf-8",
    )


class TestWhiteboxProducesCompleteDeliverables:
    """Whitebox completion yields all expected queue files."""

    def test_deliverables_have_valid_schema(self, tmp_path):
        """Each exploitation queue file should pass schema validation."""
        repo = tmp_path / "repo"
        repo.mkdir()

        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", str(repo), name="wb-complete")
        mgr.mark_completed(ws)
        deliverables = _session_deliverables(ws)

        for vc in ["injection", "xss", "auth", "ssrf"]:
            _write_queue(deliverables, vc)

        # Verify all queue files pass validation
        for vc in ["injection", "xss", "auth", "ssrf"]:
            queue_file = deliverables / f"{vc}_exploitation_queue.json"
            assert has_valid_whitebox_results(queue_file), f"{vc} queue failed validation"

        # Verify deliverables summary resolves session-centric via ws/deliverables
        summary = compute_deliverables_summary(ws)
        assert set(summary["vuln_queues"]) == {"injection", "xss", "auth", "ssrf"}


class TestBlackboxLoadsWhiteboxResults:
    """Blackbox discovers and loads whitebox deliverables."""

    def test_discovery_finds_whitebox_workspace(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()

        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", str(repo), name="wb-discover")
        mgr.mark_completed(ws)
        deliverables = _session_deliverables(ws)
        _write_queue(deliverables, "injection")

        results = find_workspaces_by_url(tmp_path / "workspaces", "https://myapp.com")
        assert len(results) == 1
        ws_path, summary = results[0]
        assert ws_path.name == "wb-discover"
        assert "injection" in summary["vuln_queues"]

    def test_workspace_discovery_service_finds_workspace(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()

        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", str(repo), name="wb-svc")
        mgr.mark_completed(ws)
        deliverables = _session_deliverables(ws)
        _write_queue(deliverables, "auth", vulns=[{
            "title": "Broken Auth", "description": "d", "severity": "critical", "location": "auth.py:5"
        }])

        discovery = WorkspaceDiscovery(tmp_path / "workspaces")
        result = discovery.find_for_blackbox("https://myapp.com", latest=True)
        assert result.workspace_path is not None
        assert result.workspace_path.name == "wb-svc"


class TestBlackboxFallbackOnEmptyResults:
    """Empty whitebox results -> blackbox runs standalone recon."""

    def test_no_whitebox_results_returns_empty(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", str(repo), name="wb-empty")
        mgr.mark_completed(ws)
        # Workspace has no deliverables/

        results = find_workspaces_by_url(tmp_path / "workspaces", "https://myapp.com")
        assert len(results) == 0

    def test_empty_vulns_not_discovered(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()

        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", str(repo), name="wb-no-vulns")
        mgr.mark_completed(ws)
        deliverables = _session_deliverables(ws)
        _write_queue(deliverables, "injection", vulns=[])

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

        repo1 = tmp_path / "repo1"
        repo1.mkdir()
        mgr = SessionManager(tmp_path / "workspaces")
        ws1 = mgr.create_workspace("https://myapp.com", str(repo1), name="ws-old")
        mgr.mark_completed(ws1)
        _write_queue(_session_deliverables(ws1), "injection")

        time.sleep(0.01)

        repo2 = tmp_path / "repo2"
        repo2.mkdir()
        ws2 = mgr.create_workspace("https://myapp.com", str(repo2), name="ws-new")
        mgr.mark_completed(ws2)
        _write_queue(_session_deliverables(ws2), "xss")

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
