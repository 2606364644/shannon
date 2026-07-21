from pathlib import Path

from supernova_core.models.audit import AgentEndResult
from supernova_core.models.metrics import SessionMetadata
from supernova_whitebox.audit.session import AuditSession
from supernova_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
from supernova_whitebox.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="s1", web_url="https://example.com", output_path=str(tmp_path))


def _read_log(tmp_path: Path) -> str:
    return (generate_audit_path(_make_meta(tmp_path)) / "workflow.log").read_text()


async def test_tool_start_reaches_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    await session.start_agent("recon", "p", attempt=1)
    lg = SessionToolAuditLogger(session, "recon", attempt=1)
    await lg.initialize()
    await lg.log_tool_start("Read", {"file_path": "/app/main.py"})
    await lg.close(success=True, duration_ms=0)
    await session.end_agent("recon", AgentEndResult(
        success=True, duration_ms=0, cost_usd=0.0, attempt_number=1))
    await session.close()
    assert "[TOOL]  recon: Read:" in _read_log(tmp_path)
    assert "file_path=/app/main.py" in _read_log(tmp_path)


async def test_assistant_turn_reaches_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    await session.start_agent("recon", "p", attempt=1)
    lg = SessionToolAuditLogger(session, "recon", attempt=1)
    await lg.initialize()
    await lg.log_assistant_turn(2, "Found sinks")
    await lg.close(success=True, duration_ms=0)
    await session.end_agent("recon", AgentEndResult(
        success=True, duration_ms=0, cost_usd=0.0, attempt_number=1))
    await session.close()
    assert "[LLM]   recon: Turn 2:" in _read_log(tmp_path)


async def test_log_error_reaches_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    lg = SessionToolAuditLogger(session, "recon", attempt=1)
    await lg.log_error("boom", turn_count=3, duration_ms=1000)
    await session.close()
    assert "[ERROR]" in _read_log(tmp_path)
    assert "boom" in _read_log(tmp_path)


async def test_initialize_creates_per_agent_log(tmp_path: Path):
    """initialize() writes the per-agent JSON header + agent_start (covers the
    migration of the old test_start_agent_creates_agent_log from test_audit_session)."""
    from supernova_whitebox.audit.utils import generate_audit_path
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    await session.start_agent("recon", "p", attempt=1)
    lg = SessionToolAuditLogger(session, "recon", attempt=1)
    await lg.initialize()
    await session.close()
    log_files = list((generate_audit_path(_make_meta(tmp_path)) / "agents").glob("*_recon_attempt-1.log"))
    assert len(log_files) == 1


async def test_close_writes_agent_end(tmp_path: Path):
    """close() writes the agent_end event to the per-agent JSON (covers the migration
    of the old test_end_agent_writes_agent_end_event from test_audit_session)."""
    import json
    from supernova_whitebox.audit.utils import generate_audit_path
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    await session.start_agent("recon", "p", attempt=1)
    lg = SessionToolAuditLogger(session, "recon", attempt=1)
    await lg.initialize()
    await lg.close(success=True, duration_ms=5000)
    await session.close()
    agent_log = list((generate_audit_path(_make_meta(tmp_path)) / "agents").glob("*.log"))[0]
    events = [json.loads(l) for l in agent_log.read_text().split("\n") if l.startswith("{")]
    end_events = [e for e in events if e["type"] == "agent_end"]
    assert len(end_events) == 1
    assert end_events[0]["data"]["success"] is True
