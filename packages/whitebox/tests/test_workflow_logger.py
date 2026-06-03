from pathlib import Path

from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentLogDetails, WorkflowSummary, AgentMetricsSummary, ResumeInfo
from shannon_whitebox.audit.workflow_logger import WorkflowLogger
from shannon_whitebox.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="test-session", web_url="https://example.com", output_path=str(tmp_path))


def _audit_dir(tmp_path: Path) -> Path:
    return generate_audit_path(_make_meta(tmp_path))


def _read_log(tmp_path: Path) -> str:
    return (_audit_dir(tmp_path) / "workflow.log").read_text()


async def test_initialize_creates_workflow_log(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize(workflow_id="wf-123")
    assert (_audit_dir(tmp_path) / "workflow.log").exists()
    content = _read_log(tmp_path)
    assert "Shannon Pentest - Workflow Log" in content
    assert "Workflow ID: wf-123" in content
    assert "Target URL:  https://example.com" in content
    await logger.close()


async def test_initialize_without_workflow_id(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    content = _read_log(tmp_path)
    assert "Shannon Pentest - Workflow Log" in content
    assert "Target URL:  https://example.com" in content
    assert "Workflow ID:" not in content
    await logger.close()


async def test_log_phase(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_phase("recon", "start")
    await logger.log_phase("recon", "complete")
    await logger.close()
    content = _read_log(tmp_path)
    assert "[PHASE] recon started" in content
    assert "[PHASE] recon completed" in content


async def test_log_agent_start(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_agent("recon", "start")
    await logger.close()
    content = _read_log(tmp_path)
    assert "[AGENT] recon started" in content


async def test_log_agent_end_with_details(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    details = AgentLogDetails(
        attempt_number=1,
        duration_ms=150000,
        cost_usd=0.05,
        success=True,
    )
    await logger.log_agent("recon", "end", details)
    await logger.close()
    content = _read_log(tmp_path)
    assert "[AGENT] recon ended" in content
    assert "2m 30s" in content
    assert "$0.0500" in content


async def test_log_agent_end_with_error(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    details = AgentLogDetails(success=False, error="Rate limit exceeded")
    await logger.log_agent("recon", "end", details)
    await logger.close()
    content = _read_log(tmp_path)
    assert "error: Rate limit exceeded" in content


async def test_log_tool_start(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_tool_start("recon", "Read", {"file_path": "/etc/passwd"})
    await logger.close()
    content = _read_log(tmp_path)
    assert "[TOOL] recon → Read(" in content
    assert "file_path=/etc/passwd" in content


async def test_log_tool_start_truncates_long_bash_command(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    long_cmd = "x" * 200
    await logger.log_tool_start("recon", "Bash", {"command": long_cmd})
    await logger.close()
    content = _read_log(tmp_path)
    assert "command=" in content
    assert "..." in content


async def test_log_llm_response(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_llm_response("recon", 1, "Found SQL injection vulnerability")
    await logger.close()
    content = _read_log(tmp_path)
    assert "[LLM] recon turn 1:" in content
    assert "Found SQL injection vulnerability" in content


async def test_log_llm_response_truncates_long_content(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    long_content = "A" * 300
    await logger.log_llm_response("recon", 1, long_content)
    await logger.close()
    content = _read_log(tmp_path)
    assert "..." in content


async def test_log_event(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_event("CUSTOM", "Something happened")
    await logger.close()
    content = _read_log(tmp_path)
    assert "[CUSTOM] Something happened" in content


async def test_log_error(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_error(ValueError("bad input"), context="parsing config")
    await logger.close()
    content = _read_log(tmp_path)
    assert "[ERROR]" in content
    assert "ValueError: bad input" in content
    assert "context: parsing config" in content


async def test_log_error_without_context(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.log_error(RuntimeError("timeout"))
    await logger.close()
    content = _read_log(tmp_path)
    assert "[ERROR]" in content
    assert "RuntimeError: timeout" in content
    assert "context:" not in content


async def test_log_workflow_complete(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize(workflow_id="wf-123")
    summary = WorkflowSummary(
        status="completed",
        total_duration_ms=330000,
        total_cost_usd=0.1234,
        completed_agents=["recon", "injection-vuln"],
        agent_metrics={
            "recon": AgentMetricsSummary(duration_ms=150000, cost_usd=0.0567),
            "injection-vuln": AgentMetricsSummary(duration_ms=180000, cost_usd=0.0234),
        },
    )
    await logger.log_workflow_complete(summary)
    await logger.close()
    content = _read_log(tmp_path)
    assert "Workflow COMPLETED" in content
    assert "Workflow ID: wf-123" in content
    assert "Status:      completed" in content
    assert "5m 30s" in content
    assert "$0.1234" in content
    assert "recon" in content
    assert "injection-vuln" in content
    assert "2m 30s" in content
    assert "3m 0s" in content


async def test_log_workflow_complete_with_error(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    summary = WorkflowSummary(
        status="failed",
        total_duration_ms=10000,
        total_cost_usd=0.01,
        completed_agents=[],
        agent_metrics={},
        error="Agent crashed unexpectedly",
    )
    await logger.log_workflow_complete(summary)
    await logger.close()
    content = _read_log(tmp_path)
    assert "failed" in content
    assert "Agent crashed unexpectedly" in content


async def test_log_resume_header(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    info = ResumeInfo(
        previous_workflow_id="wf-old",
        new_workflow_id="wf-new",
        checkpoint_hash="abc123",
        completed_agents=["recon"],
    )
    await logger.log_resume_header(info)
    await logger.close()
    content = _read_log(tmp_path)
    assert "[RESUME]" in content
    assert "wf-old" in content
    assert "wf-new" in content
    assert "abc123" in content
    assert "recon" in content


async def test_close_prevents_further_writes(tmp_path: Path):
    meta = _make_meta(tmp_path)
    logger = WorkflowLogger(meta)
    await logger.initialize()
    await logger.close()
    # These should silently do nothing after close
    await logger.log_phase("test", "start")
    await logger.log_event("TEST", "message")
