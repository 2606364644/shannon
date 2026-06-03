import json
from pathlib import Path

from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentEndResult
from shannon_whitebox.audit.metrics_tracker import MetricsTracker
from shannon_whitebox.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="test-session", web_url="https://example.com", output_path=str(tmp_path))


def _audit_dir(tmp_path: Path) -> Path:
    return generate_audit_path(_make_meta(tmp_path))


def _read_session_json(tmp_path: Path) -> dict:
    return json.loads((_audit_dir(tmp_path) / "session.json").read_text())


async def test_initialize_creates_session_json(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize(workflow_id="wf-123")
    data = _read_session_json(tmp_path)
    assert data["session"]["id"] == "test-session"
    assert data["session"]["status"] == "in-progress"
    assert data["session"]["originalWorkflowId"] == "wf-123"
    assert data["session"]["webUrl"] == "https://example.com"
    assert "createdAt" in data["session"]
    assert data["session"]["resumeAttempts"] == []
    assert "metrics" in data


async def test_initialize_without_workflow_id(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    data = _read_session_json(tmp_path)
    assert data["session"]["originalWorkflowId"] is None


async def test_start_agent_records_agent(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    metrics = tracker.get_metrics()
    assert "recon" in metrics["agents"]
    assert metrics["agents"]["recon"]["attempts"] == 1


async def test_end_agent_updates_metrics(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    result = AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05, model="claude-sonnet-4-6")
    await tracker.end_agent("recon", result)
    data = _read_session_json(tmp_path)
    assert data["metrics"]["agents"]["recon"]["duration_ms"] == 5000
    assert data["metrics"]["agents"]["recon"]["cost_usd"] == 0.05
    assert data["metrics"]["agents"]["recon"]["success"] is True
    assert data["metrics"]["agents"]["recon"]["model"] == "claude-sonnet-4-6"
    assert data["metrics"]["total_duration_ms"] == 5000
    assert data["metrics"]["total_cost_usd"] == 0.05


async def test_end_agent_accumulates_totals(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05))
    tracker.start_agent("injection", 1)
    await tracker.end_agent("injection", AgentEndResult(success=True, duration_ms=3000, cost_usd=0.03))
    data = _read_session_json(tmp_path)
    assert data["metrics"]["total_duration_ms"] == 8000
    assert data["metrics"]["total_cost_usd"] == 0.08


async def test_end_agent_with_error(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    result = AgentEndResult(success=False, duration_ms=1000, cost_usd=0.01, error="Rate limited")
    await tracker.end_agent("recon", result)
    data = _read_session_json(tmp_path)
    assert data["metrics"]["agents"]["recon"]["error"] == "Rate limited"


async def test_update_session_status(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    await tracker.update_session_status("completed")
    data = _read_session_json(tmp_path)
    assert data["session"]["status"] == "completed"


async def test_add_resume_attempt(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    await tracker.add_resume_attempt("wf-new", ["recon"], checkpoint="hash123")
    data = _read_session_json(tmp_path)
    assert len(data["session"]["resumeAttempts"]) == 1
    attempt = data["session"]["resumeAttempts"][0]
    assert attempt["workflowId"] == "wf-new"
    assert attempt["terminatedAgents"] == ["recon"]
    assert attempt["checkpoint"] == "hash123"


async def test_add_resume_attempt_without_checkpoint(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    await tracker.add_resume_attempt("wf-new", ["recon"])
    data = _read_session_json(tmp_path)
    assert data["session"]["resumeAttempts"][0]["checkpoint"] is None


async def test_multiple_resume_attempts(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    await tracker.add_resume_attempt("wf-2", ["recon"])
    await tracker.add_resume_attempt("wf-3", ["recon", "injection"])
    data = _read_session_json(tmp_path)
    assert len(data["session"]["resumeAttempts"]) == 2


async def test_reload_reads_from_disk(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    # Simulate external modification
    data = _read_session_json(tmp_path)
    data["session"]["status"] = "externally-modified"
    session_path = _audit_dir(tmp_path) / "session.json"
    session_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    await tracker.reload()
    # get_metrics is from in-memory state; verify through internal _data
    assert tracker._data["session"]["status"] == "externally-modified"


async def test_get_metrics_returns_dict(tmp_path: Path):
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    tracker.start_agent("recon", 1)
    await tracker.end_agent("recon", AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05))
    metrics = tracker.get_metrics()
    assert "agents" in metrics
    assert "total_duration_ms" in metrics
    assert metrics["total_duration_ms"] == 5000


async def test_atomic_write_uses_temp_file(tmp_path: Path):
    """Verify no stale .tmp files remain after write."""
    meta = _make_meta(tmp_path)
    tracker = MetricsTracker(meta)
    await tracker.initialize()
    # After initialize, no .tmp file should remain
    audit_dir = _audit_dir(tmp_path)
    tmp_files = list(audit_dir.glob("*.tmp"))
    assert len(tmp_files) == 0
