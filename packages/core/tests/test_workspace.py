import json
from pathlib import Path

import pytest

from shannon_core.workspace import (
    compute_deliverables_summary,
    find_latest_workspace,
    find_workspaces_by_url,
    get_workspace_age_human,
    get_workspace_info,
    get_workspace_vuln_counts,
    normalize_url,
    urls_match,
)


class TestNormalizeUrl:
    def test_strips_trailing_slash(self):
        assert normalize_url("https://example.com/") == "https://example.com"

    def test_strips_default_port_443(self):
        assert normalize_url("https://example.com:443/path") == "https://example.com/path"

    def test_strips_default_port_80(self):
        assert normalize_url("http://example.com:80/path") == "http://example.com/path"

    def test_keeps_non_default_port(self):
        assert normalize_url("https://example.com:8443/path") == "https://example.com:8443/path"

    def test_lowercase_hostname(self):
        assert normalize_url("https://Example.COM/Path") == "https://example.com/Path"

    def test_removes_fragment(self):
        assert normalize_url("https://example.com/page#section") == "https://example.com/page"


class TestUrlsMatch:
    def test_exact_match(self):
        assert urls_match("https://example.com", "https://example.com") is True

    def test_scheme_tolerated(self):
        assert urls_match("http://example.com", "https://example.com") is True

    def test_trailing_slash_ignored(self):
        assert urls_match("https://example.com/", "https://example.com") is True

    def test_different_hosts(self):
        assert urls_match("https://example.com", "https://api.example.com") is False

    def test_different_ports(self):
        assert urls_match("https://example.com:8443", "https://example.com:9443") is False

    def test_path_prefix_match(self):
        assert urls_match("https://example.com/app", "https://example.com/app/api") is True

    def test_path_no_match(self):
        assert urls_match("https://example.com/app", "https://example.com/other") is False

    def test_default_port_vs_no_port(self):
        assert urls_match("https://example.com:443", "https://example.com") is True

    def test_non_default_port_vs_no_port_matches(self):
        """Non-default explicit port matches URL without port (permissive matching)."""
        assert urls_match("https://example.com:8443", "https://example.com") is True


class TestGetWorkspaceInfo:
    def test_returns_expected_keys(self, tmp_path):
        from shannon_core.session import SessionManager

        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", "/repo", name="test-ws", scan_type="whitebox")
        mgr.mark_completed(ws)

        info = get_workspace_info(ws)
        assert info["name"] == "test-ws"
        assert info["scan_type"] == "whitebox"
        assert info["status"] == "completed"
        assert info["web_url"] == "https://myapp.com"
        assert info["repo_path"] == "/repo"
        assert info["created_at"] is not None
        assert info["completed_at"] is not None
        assert "parent_workspace" in info["links"]
        assert "deliverables_summary" in info

    def test_includes_deliverables(self, tmp_path):
        from shannon_core.session import SessionManager

        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", "/repo", name="test-ws2")
        deliverables = ws / "deliverables"
        deliverables.mkdir()
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
        )

        info = get_workspace_info(ws)
        assert "injection" in info["deliverables_summary"]["vuln_queues"]


class TestComputeDeliverablesSummary:
    def test_empty_workspace(self, tmp_path):
        ws = tmp_path / "workspaces" / "test-ws"
        ws.mkdir(parents=True)
        summary = compute_deliverables_summary(ws)
        assert summary == {"vuln_queues": [], "reports": []}

    def test_valid_queue_file(self, tmp_path):
        ws = tmp_path / "workspaces" / "test-ws"
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
        )
        summary = compute_deliverables_summary(ws)
        assert "injection" in summary["vuln_queues"]

    def test_empty_queue_file_ignored(self, tmp_path):
        ws = tmp_path / "workspaces" / "test-ws"
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "xss_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": []}), encoding="utf-8"
        )
        summary = compute_deliverables_summary(ws)
        assert "xss" not in summary["vuln_queues"]

    def test_invalid_json_ignored(self, tmp_path):
        ws = tmp_path / "workspaces" / "test-ws"
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "auth_exploitation_queue.json").write_text("not json", encoding="utf-8")
        summary = compute_deliverables_summary(ws)
        assert "auth" not in summary["vuln_queues"]

    def test_reports_collected(self, tmp_path):
        ws = tmp_path / "workspaces" / "test-ws"
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "executive_summary.md").write_text("# Summary", encoding="utf-8")
        (deliverables / "injection_findings.md").write_text("# Findings", encoding="utf-8")
        summary = compute_deliverables_summary(ws)
        assert "executive_summary.md" in summary["reports"]
        assert "injection_findings.md" in summary["reports"]

    def test_multiple_vuln_queues(self, tmp_path):
        ws = tmp_path / "workspaces" / "test-ws"
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        for vc in ["injection", "xss", "auth"]:
            (deliverables / f"{vc}_exploitation_queue.json").write_text(
                json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
            )
        summary = compute_deliverables_summary(ws)
        assert set(summary["vuln_queues"]) == {"injection", "xss", "auth"}


def _create_workspace_with_queues(
    tmp_path: Path, name: str, web_url: str, scan_type: str, vuln_classes: list[str]
) -> Path:
    """Helper: create a workspace with valid exploitation queue files."""
    from shannon_core.session import SessionManager

    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace(web_url, "/repo", name=name, scan_type=scan_type)
    mgr.mark_completed(ws)

    deliverables = ws / "deliverables"
    deliverables.mkdir(parents=True)
    for vc in vuln_classes:
        (deliverables / f"{vc}_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
        )
    return ws


class TestFindLatestWorkspace:
    def test_finds_most_recent_whitebox(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        _create_workspace_with_queues(tmp_path, "ws-old", "https://old.com", "whitebox", ["injection"])
        import time
        time.sleep(0.01)
        _create_workspace_with_queues(tmp_path, "ws-new", "https://new.com", "whitebox", ["xss"])
        result = find_latest_workspace(ws_dir)
        assert result is not None
        assert result.name == "ws-new"

    def test_skips_blackbox(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        _create_workspace_with_queues(tmp_path, "bb-ws", "https://test.com", "blackbox", ["injection"])
        result = find_latest_workspace(ws_dir, scan_type="whitebox")
        assert result is None

    def test_skips_empty_deliverables(self, tmp_path):
        from shannon_core.session import SessionManager
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://empty.com", "/repo", name="empty-ws")
        mgr.mark_completed(ws)
        result = find_latest_workspace(tmp_path / "workspaces")
        assert result is None

    def test_no_workspaces(self, tmp_path):
        result = find_latest_workspace(tmp_path / "workspaces")
        assert result is None

    def test_url_prioritization(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        _create_workspace_with_queues(tmp_path, "ws-other", "https://other.com", "whitebox", ["injection"])
        import time
        time.sleep(0.01)
        _create_workspace_with_queues(tmp_path, "ws-target", "https://target.com", "whitebox", ["xss"])
        result = find_latest_workspace(ws_dir, url="https://target.com")
        assert result is not None
        assert result.name == "ws-target"


class TestFindWorkspacesByUrl:
    def test_finds_matching_workspaces(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        _create_workspace_with_queues(tmp_path, "ws1", "https://myapp.com", "whitebox", ["injection"])
        _create_workspace_with_queues(tmp_path, "ws2", "https://other.com", "whitebox", ["xss"])
        _create_workspace_with_queues(tmp_path, "ws3", "https://myapp.com", "whitebox", ["auth"])
        results = find_workspaces_by_url(ws_dir, "https://myapp.com")
        assert len(results) == 2
        names = [r[0].name for r in results]
        assert "ws1" in names
        assert "ws3" in names

    def test_scheme_tolerant(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        _create_workspace_with_queues(tmp_path, "ws-http", "https://myapp.com", "whitebox", ["injection"])
        results = find_workspaces_by_url(ws_dir, "http://myapp.com")
        assert len(results) == 1

    def test_excludes_no_deliverables(self, tmp_path):
        from shannon_core.session import SessionManager
        mgr = SessionManager(tmp_path / "workspaces")
        mgr.create_workspace("https://myapp.com", "/repo", name="empty-ws")
        results = find_workspaces_by_url(tmp_path / "workspaces", "https://myapp.com")
        assert len(results) == 0

    def test_no_matches(self, tmp_path):
        ws_dir = tmp_path / "workspaces"
        _create_workspace_with_queues(tmp_path, "ws1", "https://myapp.com", "whitebox", ["injection"])
        results = find_workspaces_by_url(ws_dir, "https://other.com")
        assert len(results) == 0


class TestGetWorkspaceVulnCounts:
    def test_returns_per_class_counts(self, tmp_path):
        ws = tmp_path / "ws"
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [
                {"title": "A", "description": "d", "severity": "high", "location": "a.py:1"},
                {"title": "B", "description": "d", "severity": "low", "location": "b.py:2"},
            ]}), encoding="utf-8"
        )
        (deliverables / "xss_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [
                {"title": "C", "description": "d", "severity": "medium", "location": "c.py:3"},
            ]}), encoding="utf-8"
        )

        counts = get_workspace_vuln_counts(ws)
        assert counts == {"injection": 2, "xss": 1}

    def test_empty_deliverables(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        counts = get_workspace_vuln_counts(ws)
        assert counts == {}


class TestGetWorkspaceAge:
    def test_returns_age_string(self, tmp_path):
        from shannon_core.session import SessionManager

        mgr = SessionManager(tmp_path / "ws")
        ws = mgr.create_workspace("https://test.com", "/repo", name="age-ws")
        mgr.mark_completed(ws)

        age = get_workspace_age_human(ws)
        assert isinstance(age, str)
        assert len(age) > 0
