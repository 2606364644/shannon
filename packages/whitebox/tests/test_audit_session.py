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


async def test_start_agent_creates_agent_log(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "Analyze the target", attempt=1)
    ad = _audit_dir(tmp_path)
    # Agent log file should exist
    log_files = list((ad / "agents").glob("*_recon_attempt-1.log"))
    assert len(log_files) == 1


async def test_start_agent_saves_prompt(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "Analyze the target", attempt=1)
    ad = _audit_dir(tmp_path)
    assert (ad / "prompts" / "recon.md").exists()


async def test_log_event_dispatches_to_both_loggers(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "prompt", attempt=1)
    await session.log_event("tool_start", {"toolName": "Read", "parameters": {"file_path": "/tmp/test"}})
    ad = _audit_dir(tmp_path)
    # Check agent log has JSON event
    agent_log = list((ad / "agents").glob("*.log"))[0]
    agent_content = agent_log.read_text()
    json_lines = [l for l in agent_content.split("\n") if l.startswith("{")]
    tool_events = [json.loads(l) for l in json_lines if '"tool_start"' in l]
    assert len(tool_events) == 1
    # Check workflow log has human-readable event
    wf_content = (ad / "workflow.log").read_text()
    assert "[TOOL] recon → Read(" in wf_content


async def test_log_event_dispatches_llm_response(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "prompt", attempt=1)
    await session.log_event("llm_response", {"turn": 1, "content": "Found XSS vulnerability"})
    ad = _audit_dir(tmp_path)
    wf_content = (ad / "workflow.log").read_text()
    assert "[LLM] recon turn 1:" in wf_content
    assert "Found XSS vulnerability" in wf_content


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


async def test_end_agent_writes_agent_end_event(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.start_agent("recon", "prompt", attempt=1)
    result = AgentEndResult(success=True, duration_ms=5000, cost_usd=0.05)
    await session.end_agent("recon", result)
    ad = _audit_dir(tmp_path)
    agent_log = list((ad / "agents").glob("*.log"))[0]
    content = agent_log.read_text()
    json_lines = [json.loads(l) for l in content.split("\n") if l.startswith("{")]
    end_events = [e for e in json_lines if e["type"] == "agent_end"]
    assert len(end_events) == 1
    assert end_events[0]["data"]["success"] is True


async def test_log_phase_start_and_complete(tmp_path: Path):
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    await session.log_phase_start("recon")
    await session.log_phase_complete("recon")
    ad = _audit_dir(tmp_path)
    wf_content = (ad / "workflow.log").read_text()
    assert "[PHASE] recon started" in wf_content
    assert "[PHASE] recon completed" in wf_content


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
    """End-to-end: initialize → start_agent → log_events → end_agent → complete."""
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize(workflow_id="wf-lifecycle")

    await session.log_phase_start("recon")
    await session.start_agent("recon", "Analyze the target application", attempt=1)
    await session.log_event("tool_start", {"toolName": "Read", "parameters": {"file_path": "/app/main.py"}})
    await session.log_event("llm_response", {"turn": 1, "content": "Identified SQL injection points"})
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
    # Verify workflow log
    wf = (ad / "workflow.log").read_text()
    assert "Shannon Pentest - Workflow Log" in wf
    assert "Workflow ID: wf-lifecycle" in wf
    assert "[PHASE] recon started" in wf
    assert "[AGENT] recon started" in wf
    assert "[TOOL] recon → Read(" in wf
    assert "[LLM] recon turn 1:" in wf
    assert "[AGENT] recon ended" in wf
    assert "[PHASE] recon completed" in wf
    assert "Workflow COMPLETED" in wf

    # Verify session.json
    data = json.loads((ad / "session.json").read_text())
    assert data["session"]["status"] == "completed"
    assert data["metrics"]["total_duration_ms"] == 15000
    assert data["metrics"]["agents"]["recon"]["success"] is True

    # Verify agent log
    agent_log = list((ad / "agents").glob("*.log"))[0]
    agent_content = agent_log.read_text()
    assert "Agent: recon" in agent_content
    json_lines = [json.loads(l) for l in agent_content.split("\n") if l.startswith("{")]
    assert len(json_lines) == 4  # agent_start + tool_start + llm_response + agent_end
