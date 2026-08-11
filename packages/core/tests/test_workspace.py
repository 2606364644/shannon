import json
from pathlib import Path

import pytest

from supernova_core.workspace import (
    compute_deliverables_summary,
    find_latest_workspace,
    find_workspaces_by_url,
    get_workspace_age_human,
    get_workspace_info,
    get_workspace_vuln_counts,
    normalize_url,
    summarize_deliverables_dir,
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
        from supernova_core.session import SessionManager

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
        from supernova_core.session import SessionManager

        repo = tmp_path / "repo"
        repo.mkdir()
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", str(repo), name="test-ws2")

        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
        )

        info = get_workspace_info(ws)
        assert "injection" in info["deliverables_summary"]["vuln_queues"]


class TestSummarizeDeliverablesDir:
    """Tests for the deliverables-dir scanner — the core of compute_deliverables_summary.

    These pass a deliverables directory directly, independent of session/repo resolution.
    """

    def test_empty_dir(self, tmp_path):
        deliverables = tmp_path / "deliverables"
        deliverables.mkdir()
        assert summarize_deliverables_dir(deliverables) == {"vuln_queues": [], "reports": []}

    def test_missing_dir(self, tmp_path):
        assert summarize_deliverables_dir(tmp_path / "nope") == {"vuln_queues": [], "reports": []}

    def test_valid_queue_file(self, tmp_path):
        deliverables = tmp_path / "deliverables"
        deliverables.mkdir()
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
        )
        assert "injection" in summarize_deliverables_dir(deliverables)["vuln_queues"]

    def test_empty_queue_file_ignored(self, tmp_path):
        deliverables = tmp_path / "deliverables"
        deliverables.mkdir()
        (deliverables / "xss_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": []}), encoding="utf-8"
        )
        assert "xss" not in summarize_deliverables_dir(deliverables)["vuln_queues"]

    def test_invalid_json_ignored(self, tmp_path):
        deliverables = tmp_path / "deliverables"
        deliverables.mkdir()
        (deliverables / "auth_exploitation_queue.json").write_text("not json", encoding="utf-8")
        assert "auth" not in summarize_deliverables_dir(deliverables)["vuln_queues"]

    def test_reports_collected(self, tmp_path):
        deliverables = tmp_path / "deliverables"
        deliverables.mkdir()
        (deliverables / "executive_summary.md").write_text("# Summary", encoding="utf-8")
        (deliverables / "injection_findings.md").write_text("# Findings", encoding="utf-8")
        reports = summarize_deliverables_dir(deliverables)["reports"]
        assert "executive_summary.md" in reports
        assert "injection_findings.md" in reports

    def test_multiple_vuln_queues(self, tmp_path):
        deliverables = tmp_path / "deliverables"
        deliverables.mkdir()
        for vc in ["injection", "xss", "auth"]:
            (deliverables / f"{vc}_exploitation_queue.json").write_text(
                json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
            )
        assert set(summarize_deliverables_dir(deliverables)["vuln_queues"]) == {"injection", "xss", "auth"}


class TestComputeDeliverablesSummarySessionCentric:
    """compute_deliverables_summary(ws) reads deliverables under the session dir (ws/deliverables)."""

    def test_finds_session_deliverables(self, tmp_path):
        from supernova_core.session import SessionManager

        repo = tmp_path / "repo"
        repo.mkdir()
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", str(repo), name="wb-1")
        # Deliverables live under the session dir, NOT under the repo.
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
        )
        assert not (repo / ".shannon" / "deliverables").exists()

        summary = compute_deliverables_summary(ws)
        assert "injection" in summary["vuln_queues"]

    def test_fallback_when_no_session(self, tmp_path):
        # Bare workspace dir without session.json → deliverables read from workspaces/<name>/<subdir>.
        ws = tmp_path / "workspaces" / "orphan"
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "xss_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [{"id": "1"}]}), encoding="utf-8"
        )
        summary = compute_deliverables_summary(ws)
        assert "xss" in summary["vuln_queues"]

    def test_empty_when_repo_has_no_deliverables(self, tmp_path):
        from supernova_core.session import SessionManager

        repo = tmp_path / "repo"
        repo.mkdir()
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://myapp.com", str(repo), name="wb-empty")
        assert compute_deliverables_summary(ws) == {"vuln_queues": [], "reports": []}


def _create_workspace_with_queues(
    tmp_path: Path, name: str, web_url: str, scan_type: str, vuln_classes: list[str]
) -> Path:
    """Helper: workspace whose queue files live session-centric (ws/deliverables) —
    matching production whitebox output (deliverables under workspaces/<session>)."""
    from supernova_core.session import SessionManager

    repo = tmp_path / "repos" / name
    repo.mkdir(parents=True)
    mgr = SessionManager(tmp_path / "workspaces")
    ws = mgr.create_workspace(web_url, str(repo), name=name, scan_type=scan_type)
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
        from supernova_core.session import SessionManager
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
        from supernova_core.session import SessionManager
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
        from supernova_core.session import SessionManager

        repo = tmp_path / "repo"
        repo.mkdir()
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://x.com", str(repo), name="ws")
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
        assert get_workspace_vuln_counts(ws) == {"injection": 2, "xss": 1}

    def test_empty_deliverables(self, tmp_path):
        from supernova_core.session import SessionManager

        repo = tmp_path / "repo"
        repo.mkdir()
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://x.com", str(repo), name="ws")
        assert get_workspace_vuln_counts(ws) == {}

    def test_counts_exploited_from_verdicts_json(self, tmp_path):
        """黑盒 scan：verdicts.json 的 exploited verdict 计入 vuln_count（spec 2026-08-12）。
        blocked/potential 不计；accepted_ids 含 3 条但 exploited 只 2 → 计 2。"""
        from supernova_core.session import SessionManager

        repo = tmp_path / "repo"
        repo.mkdir()
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://x.com", str(repo), name="bb")
        deliverables = ws / "deliverables" / "blackbox"
        deliverables.mkdir(parents=True)
        (deliverables / "injection_exploit_verdicts.json").write_text(
            json.dumps({
                "vuln_class": "injection",
                "accepted_ids": ["INJ-1", "INJ-2", "INJ-3"],
                "verdicts": [
                    {"vulnerability_id": "INJ-1", "status": "exploited"},
                    {"vulnerability_id": "INJ-2", "status": "blocked_by_security"},
                    {"vulnerability_id": "INJ-3", "status": "exploited"},
                ],
                "rejected": []}), encoding="utf-8")
        assert get_workspace_vuln_counts(ws) == {"injection": 2}

    def test_verdicts_and_queue_do_not_collide(self, tmp_path):
        """同 class 的 queue(白盒) 与 verdicts(黑盒) 共存时累加不互吞（用 +=）。
        实际同 scan 不共存，此测锁 += 语义防未来回归。"""
        from supernova_core.session import SessionManager

        repo = tmp_path / "repo"
        repo.mkdir()
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://x.com", str(repo), name="mix")
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [
                {"title": "A"}, {"title": "B"}]}), encoding="utf-8")  # 白盒 2 条
        (deliverables / "blackbox").mkdir()
        (deliverables / "blackbox" / "injection_exploit_verdicts.json").write_text(
            json.dumps({"vuln_class": "injection", "accepted_ids": ["INJ-1"],
                        "verdicts": [{"vulnerability_id": "INJ-1", "status": "exploited"}],
                        "rejected": []}), encoding="utf-8")  # 黑盒 exploited 1
        assert get_workspace_vuln_counts(ws) == {"injection": 3}  # 2 (queue) + 1 (exploited)


class TestGetWorkspaceAge:
    def test_returns_age_string(self, tmp_path):
        from supernova_core.session import SessionManager

        mgr = SessionManager(tmp_path / "ws")
        ws = mgr.create_workspace("https://test.com", "/repo", name="age-ws")
        mgr.mark_completed(ws)

        age = get_workspace_age_human(ws)
        assert isinstance(age, str)
        assert len(age) > 0
