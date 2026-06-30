import json
from contextlib import asynccontextmanager

import pytest

from shannon_whitebox.audit.session_registry import (
    clear_audit_session,
    set_audit_session,
)
from shannon_whitebox.pipeline import activities


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
async def test_scan_writes_config_json_and_gitnexus_queue(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text(
        "app.post('/login', (req, res) => { res.cookie('session', t); });\n"
    )
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)

    monkeypatch.setattr(activities, "_get_paths", lambda i: (repo, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_auth_config_scan(_input(repo))
    finally:
        clear_audit_session()

    scan_path = deliverables / "auth_config_scan.json"
    queue_path = deliverables / "auth_gitnexus_queue.json"
    assert scan_path.exists()
    assert queue_path.exists()

    scan = json.loads(scan_path.read_text())
    assert "cookie_findings" in scan
    assert len(scan["cookie_findings"]) >= 1

    queue = json.loads(queue_path.read_text())
    assert "vulnerabilities" in queue
    assert len(queue["vulnerabilities"]) >= 1
    v = queue["vulnerabilities"][0]
    assert v["vulnerability_type"] in (
        "Authentication_Bypass", "Session_Management_Flaw",
        "Transport_Exposure", "Abuse_Defenses_Missing",
        "OAuth_Flow_Issue", "Token_Management_Issue",
        "Login_Flow_Logic", "Reset_Recovery_Flaw",
    )
    assert v["source_track"] == "gitnexus"
    assert v["externally_exploitable"] is True


@pytest.mark.asyncio
async def test_scan_zero_findings_writes_empty_files(tmp_path, monkeypatch):
    """Zero findings still writes both files (empty) — merger degrades to llm-only."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text("const x = 1;\n")  # nothing suspicious
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)

    monkeypatch.setattr(activities, "_get_paths", lambda i: (repo, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_auth_config_scan(_input(repo))
    finally:
        clear_audit_session()

    scan = json.loads((deliverables / "auth_config_scan.json").read_text())
    queue = json.loads((deliverables / "auth_gitnexus_queue.json").read_text())
    assert scan["cookie_findings"] == []
    assert queue["vulnerabilities"] == []
    assert result["total_findings"] == 0


@pytest.mark.asyncio
async def test_scan_does_not_crash_on_empty_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)

    monkeypatch.setattr(activities, "_get_paths", lambda i: (repo, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        result = await activities.run_auth_config_scan(_input(repo))
    finally:
        clear_audit_session()
    assert result["total_findings"] == 0
    assert (deliverables / "auth_config_scan.json").exists()


@pytest.mark.asyncio
async def test_finding_category_maps_to_vulnerability_type(tmp_path, monkeypatch):
    """Each scanner category maps to a sensible AUTH-VULN vulnerability_type."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text(
        "res.cookie('s', t);              // cookie\n"
        "app.use(cors({ origin: '*' }));  // cors\n"
        "app.post('/login', (req,res)=>{}); // rate_limit\n"
    )
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)

    monkeypatch.setattr(activities, "_get_paths", lambda i: (repo, deliverables, tmp_path))
    set_audit_session(_RecordingSession())
    try:
        await activities.run_auth_config_scan(_input(repo))
    finally:
        clear_audit_session()

    queue = json.loads((deliverables / "auth_gitnexus_queue.json").read_text())
    types = {v["vulnerability_type"] for v in queue["vulnerabilities"]}
    assert "Session_Management_Flaw" in types  # from cookie
