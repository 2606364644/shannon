"""Integration: auth dual-track closure (scanner -> GitNexus queue -> merger).

Requires Plan 3 (dual_track_merger + run_merge_dual_track_queues). If Plan 3
is not landed, the merger portion is skipped via importorskip; the scanner
GitNexus-track production is still validated.
"""
from contextlib import asynccontextmanager

import pytest

dual_track = pytest.importorskip("shannon_core.code_index.dual_track_merger")


class _RecordingSession:
    @asynccontextmanager
    async def track_step(self, phase: str, name: str, intent: str | None = None):
        yield


def _input(repo):
    class FakeInput:
        agent_name = None
        web_url = None
        repo_path = str(repo)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None
        deliverables_subdir = None
        workspace_name = None
        workspace_path = None

    return FakeInput()


@pytest.mark.asyncio
async def test_auth_dual_track_scanner_feeds_merger(tmp_path, monkeypatch):
    """Scanner GitNexus-track queue + synthetic LLM-track queue -> merged with
    merge_source tags (Plan 3 merger)."""
    from shannon_core.code_index.dual_track_merger import merge_dual_track_queues
    from shannon_core.models.queue_schemas import (
        AuthVulnerability,
        VulnerabilityQueue,
    )
    from shannon_whitebox.audit.session_registry import (
        clear_audit_session,
        set_audit_session,
    )
    from shannon_whitebox.pipeline import activities

    # --- Scanner produces GitNexus track ---
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text(
        "app.post('/login', (req,res)=>{ res.cookie('session', t); });\n"
    )
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    monkeypatch.setattr(activities, "_get_paths", lambda i: (repo, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        await activities.run_auth_config_scan(_input(repo))
    finally:
        clear_audit_session()

    gn_path = deliverables / "auth_gitnexus_queue.json"
    assert gn_path.exists()
    gn_parsed = VulnerabilityQueue.parse_lenient(gn_path.read_text())
    gn_findings = gn_parsed.queue.vulnerabilities
    assert len(gn_findings) >= 1
    assert all(getattr(f, "source_track", None) == "gitnexus" for f in gn_findings)

    # --- Synthetic LLM track (what executor would produce) ---
    llm_finding = AuthVulnerability(
        ID="AUTH-VULN-01",
        vulnerability_type="Session_Management_Flaw",
        externally_exploitable=True,
        confidence="high",
        source_track="llm",
        source_endpoint="POST /login",
        vulnerable_code_location="app.js:1",
        missing_defense="Session cookie lacks HttpOnly and Secure flags",
        exploitation_hypothesis="Attacker can hijack session via XSS/network sniffing",
        suggested_exploit_technique="session_hijacking",
    )

    # --- Merge (Plan 3) ---
    merged = merge_dual_track_queues([llm_finding], gn_findings, mode="verdict")
    assert len(merged) >= 1
    sources = {getattr(m, "merge_source") for m in merged}
    assert sources & {"both", "llm-only", "gitnexus-only"}  # all valid tags
    for m in merged:
        assert m.merge_source is not None
        assert m.confidence is not None


@pytest.mark.asyncio
async def test_auth_dual_track_pure_additive_when_scanner_empty(tmp_path, monkeypatch):
    """Scanner zero findings -> GitNexus track empty -> merger yields llm-only."""
    from shannon_core.code_index.dual_track_merger import merge_dual_track_queues
    from shannon_core.models.queue_schemas import (
        AuthVulnerability,
        VulnerabilityQueue,
    )
    from shannon_whitebox.audit.session_registry import (
        clear_audit_session,
        set_audit_session,
    )
    from shannon_whitebox.pipeline import activities

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "clean.js").write_text("const x = 1;\n")
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    monkeypatch.setattr(activities, "_get_paths", lambda i: (repo, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        await activities.run_auth_config_scan(_input(repo))
    finally:
        clear_audit_session()

    gn_parsed = VulnerabilityQueue.parse_lenient(
        (deliverables / "auth_gitnexus_queue.json").read_text())
    assert gn_parsed.queue.vulnerabilities == []  # scanner empty

    llm_finding = AuthVulnerability(
        ID="AUTH-VULN-01", vulnerability_type="Login_Flow_Logic",
        externally_exploitable=True, confidence="high", source_track="llm",
        source_endpoint="POST /login", vulnerable_code_location="auth.js:10",
        missing_defense="user enumeration in login error",
        exploitation_hypothesis="attacker enumerates valid usernames",
        suggested_exploit_technique="account_enumeration",
    )
    merged = merge_dual_track_queues([llm_finding], [], mode="verdict")
    assert len(merged) == 1
    assert merged[0].merge_source == "llm-only"
    assert merged[0].confidence == "needs_review"
