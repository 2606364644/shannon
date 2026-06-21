import json
from pathlib import Path

from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentEndResult, WorkflowSummary, AgentMetricsSummary, ResumeInfo
from shannon_whitebox.audit.session import AuditSession
from shannon_whitebox.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="test-session", web_url="https://example.com", output_path=str(tmp_path))


def _audit_dir(tmp_path: Path) -> Path:
    return generate_audit_path(_make_meta(tmp_path))


async def test_initialize_creates_directories(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize(workflow_id="wf-1")
    ad = _audit_dir(tmp_path)
    assert (ad / "agents").is_dir()
    assert (ad / "prompts").is_dir()
    assert (ad / "deliverables").is_dir()
    assert (ad / "workflow.log").exists()
    assert (ad / "session.json").exists()


async def test_start_agent_saves_prompt(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "Analyze the target", attempt=1)
    ad = _audit_dir(tmp_path)
    assert (ad / "prompts" / "recon.md").exists()


async def test_end_agent_updates_metrics(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "prompt", attempt=1)
    result = AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05, model="claude-sonnet-4-6")
    await session.end_agent("recon", result)
    ad = _audit_dir(tmp_path)
    data = json.loads((ad / "session.json").read_text())
    assert data["metrics"]["agents"]["recon"]["success"] is True
    assert data["metrics"]["total_cost_usd"] == 0.05


async def test_log_phase_start_and_complete(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.log_phase_start("recon")
    await session.log_phase_complete("recon")
    ad = _audit_dir(tmp_path)
    wf_content = (ad / "workflow.log").read_text()
    assert "[PHASE] Starting recon" in wf_content
    assert "[PHASE] Completed recon" in wf_content


async def test_log_workflow_complete(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize(workflow_id="wf-1")
    summary = WorkflowSummary(
        status="completed",
        total_duration_ms=300000,
        total_cost_usd=0.12,
        completed_agents=["recon"],
        agent_metrics={"recon": AgentMetricsSummary(duration_ms=300000, cost_usd=0.12)},
    )
    await session.log_workflow_complete(summary)
    ad = _audit_dir(tmp_path)
    wf_content = (ad / "workflow.log").read_text()
    assert "Workflow COMPLETED" in wf_content
    # Check session status updated
    data = json.loads((ad / "session.json").read_text())
    assert data["session"]["status"] == "completed"


async def test_update_session_status(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.update_session_status("paused")
    ad = _audit_dir(tmp_path)
    data = json.loads((ad / "session.json").read_text())
    assert data["session"]["status"] == "paused"


async def test_add_resume_attempt(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.add_resume_attempt("wf-2", ["recon"], checkpoint="hash123")
    ad = _audit_dir(tmp_path)
    data = json.loads((ad / "session.json").read_text())
    assert len(data["session"]["resumeAttempts"]) == 1
    assert data["session"]["resumeAttempts"][0]["workflowId"] == "wf-2"


async def test_log_resume_header(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    info = ResumeInfo(
        previous_workflow_id="wf-old",
        new_workflow_id="wf-new",
        checkpoint_hash="abc123",
        completed_agents=["recon"],
    )
    await session.log_resume_header(info)
    ad = _audit_dir(tmp_path)
    wf_content = (ad / "workflow.log").read_text()
    assert "[RESUME]" in wf_content
    assert "wf-old" in wf_content


async def test_get_metrics(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "prompt", attempt=1)
    await session.end_agent("recon", AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05))
    metrics = await session.get_metrics()
    assert metrics["total_duration_ms"] == 5000
    assert metrics["total_cost_usd"] == 0.05


async def test_full_lifecycle(tmp_path: Path):
    """End-to-end: initialize → start_agent → logger events → end_agent → complete."""
    from shannon_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize(workflow_id="wf-lifecycle")

    await session.log_phase_start("recon")
    await session.start_agent("recon", "Analyze the target application", attempt=1)
    lg = SessionToolAuditLogger(session, "recon", attempt=1)
    await lg.initialize()
    await lg.log_tool_start("Read", {"file_path": "/app/main.py"})
    await lg.log_assistant_turn(1, "Identified SQL injection points")
    await lg.close(success=True, duration_ms=15000)
    await session.end_agent("recon", AgentEndResult(success=True, duration_ms=15000, cost_usd=0.08))
    await session.log_phase_complete("recon")

    summary = WorkflowSummary(
        status="completed",
        total_duration_ms=15000,
        total_cost_usd=0.08,
        completed_agents=["recon"],
        agent_metrics={"recon": AgentMetricsSummary(duration_ms=15000, cost_usd=0.08)},
    )
    await session.log_workflow_complete(summary)

    ad = _audit_dir(tmp_path)
    wf = (ad / "workflow.log").read_text()
    assert "Shannon Pentest - Workflow Log" in wf
    assert "Workflow ID: wf-lifecycle" in wf
    assert "[PHASE] Starting recon" in wf
    assert "[AGENT] recon: Starting" in wf
    assert "[TOOL]  recon: Read:" in wf
    assert "[LLM]   recon: Turn 1:" in wf
    assert "[AGENT] recon: Completed" in wf
    assert "[PHASE] Completed recon" in wf
    assert "Workflow COMPLETED" in wf

    data = json.loads((ad / "session.json").read_text())
    assert data["session"]["status"] == "completed"
    assert data["metrics"]["total_duration_ms"] == 15000
    assert data["metrics"]["agents"]["recon"]["success"] is True

    agent_log = list((ad / "agents").glob("*.log"))[0]
    agent_content = agent_log.read_text()
    assert "Agent: recon" in agent_content
    json_lines = [json.loads(l) for l in agent_content.split("\n") if l.startswith("{")]
    assert len(json_lines) == 4  # agent_start + tool_start + llm_response + agent_end


async def test_display_config_passed_to_workflow_logger(tmp_path: Path):
    import io
    from rich.console import Console
    from shannon_core.display.live_dashboard import LiveDashboardRenderer
    meta = _make_meta(tmp_path)
    console = Console(file=io.StringIO(), width=100)
    dashboard = LiveDashboardRenderer(console)
    session = AuditSession(meta, use_rich=True, console=console, dashboard=dashboard)
    await session.initialize(workflow_id="wf-1")
    assert session._workflow_logger._use_rich is True
    assert len(session._workflow_logger._dispatcher._renderers) == 3


async def test_log_step_writes_step_line(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.log_step("code-index", "pre-recon", "start")
    await session.log_step("code-index", "pre-recon", "complete", duration_ms=9000)
    ad = _audit_dir(tmp_path)
    wf = (ad / "workflow.log").read_text()
    assert "[STEP]" in wf
    assert "code-index" in wf


async def test_log_phase_start_passes_steps(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.log_phase_start("pre-recon", steps=("code-index", "pre-recon"))
    ad = _audit_dir(tmp_path)
    assert "[PHASE] Starting pre-recon" in (ad / "workflow.log").read_text()


async def test_track_step_emits_start_then_complete(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    async with session.track_step("pre-recon", "merge-sinks"):
        pass
    wf = (_audit_dir(tmp_path) / "workflow.log").read_text()
    assert "[STEP] merge-sinks: Starting" in wf
    assert "[STEP] merge-sinks: Completed" in wf


async def test_track_step_emits_complete_with_error_on_exception(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    import pytest
    with pytest.raises(RuntimeError):
        async with session.track_step("pre-recon", "adjudication"):
            raise RuntimeError("boom")
    wf = (_audit_dir(tmp_path) / "workflow.log").read_text()
    assert "[STEP] adjudication: Starting" in wf
    assert "boom" in wf   # error surfaced in the complete step line
