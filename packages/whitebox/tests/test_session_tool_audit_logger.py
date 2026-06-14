from pathlib import Path

from shannon_core.models.metrics import SessionMetadata
from shannon_whitebox.audit.session import AuditSession
from shannon_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
from shannon_whitebox.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="s1", web_url="https://example.com", output_path=str(tmp_path))


def _read_log(tmp_path: Path) -> str:
    return (generate_audit_path(_make_meta(tmp_path)) / "workflow.log").read_text()


async def test_tool_start_reaches_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    await session.start_agent("recon", "p", attempt=1)
    lg = SessionToolAuditLogger(session)
    await lg.log_tool_start("Read", {"file_path": "/app/main.py"})
    await session.close()
    assert "[TOOL]  recon: Read:" in _read_log(tmp_path)
    assert "file_path=/app/main.py" in _read_log(tmp_path)


async def test_assistant_turn_reaches_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    await session.start_agent("recon", "p", attempt=1)
    lg = SessionToolAuditLogger(session)
    await lg.log_assistant_turn(2, "Found sinks")
    await session.close()
    assert "[LLM]   recon: Turn 2:" in _read_log(tmp_path)


async def test_log_error_reaches_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    lg = SessionToolAuditLogger(session)
    await lg.log_error("boom", turn_count=3, duration_ms=1000)
    await session.close()
    assert "[ERROR]" in _read_log(tmp_path)
    assert "boom" in _read_log(tmp_path)
