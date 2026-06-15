"""L3: tool/llm events through SessionToolAuditLogger reach workflow.log via the
blackbox AuditSession -> WorkflowLogger -> dispatcher -> FileLogRenderer path."""
from pathlib import Path

from shannon_core.models.audit import AgentEndResult
from shannon_core.models.metrics import SessionMetadata
from shannon_core.audit.session import AuditSession
from shannon_core.audit.session_registry import set_audit_session, clear_audit_session
from shannon_core.audit.session_tool_audit_logger import SessionToolAuditLogger
from shannon_core.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="s1", web_url="https://example.com", output_path=str(tmp_path))


async def test_session_tool_audit_logger_feeds_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    set_audit_session(session)
    try:
        await session.start_agent("injection-exploit", "p", attempt=1)
        lg = SessionToolAuditLogger(session)
        await lg.log_tool_start("Bash", {"command": "curl 'http://x/?q=<script>'"})
        await lg.log_assistant_turn(1, "confirmed reflected XSS")
        await session.end_agent("injection-exploit", AgentEndResult(
            success=True, duration_ms=100, cost_usd=0.01, attempt_number=1))
    finally:
        clear_audit_session()
        await session.close()
    wf = (generate_audit_path(_make_meta(tmp_path)) / "workflow.log").read_text()
    assert "[AGENT] [Injection] injection-exploit: Starting" in wf
    assert "[TOOL]  [Injection] injection-exploit: Bash:" in wf
    assert "[LLM]   [Injection] injection-exploit: Turn 1:" in wf
    assert "[AGENT] [Injection] injection-exploit: Completed" in wf
