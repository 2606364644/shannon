import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from supernova_core.models.metrics import AgentMetrics, SessionMetadata
from supernova_core.audit.session import AuditSession
from supernova_core.audit.session_registry import set_audit_session, clear_audit_session
from supernova_core.audit.session_tool_audit_logger import SessionToolAuditLogger
from supernova_blackbox.pipeline.shared import BlackboxActivityInput


@pytest.mark.asyncio
async def test_run_recon_wires_tool_audit_logger(tmp_path, monkeypatch):
    """run_recon derives a SessionToolAuditLogger from the AuditSession and passes
    it through to ReconExecutor (NOT audit_logger — that was dropped in Task 3)."""
    from supernova_blackbox.pipeline import activities

    deliverables_root = tmp_path
    meta = SessionMetadata(id="s1", web_url="https://example.com", output_path=str(tmp_path))
    session = AuditSession(meta)
    await session.initialize()
    set_audit_session(session)

    mock_recon = MagicMock()
    mock_recon.execute = AsyncMock(
        return_value=AgentMetrics(duration_ms=1, cost_usd=0.0, num_turns=1, model="m")
    )
    # activity.info() must return an object whose .attempt is an int (real
    # Temporal context is absent under direct invocation).
    monkeypatch.setattr("temporalio.activity.info", lambda: MagicMock(attempt=1))
    try:
        with patch("supernova_blackbox.pipeline.activities.resolve_deliverables_path",
                   return_value=deliverables_root / "deliverables"), \
             patch("supernova_blackbox.agents.recon_executor.ReconExecutor", return_value=mock_recon):
            inp = BlackboxActivityInput(web_url="https://example.com", workspace_name="recon-blackbox")
            await activities.run_recon(inp)

        kwargs = mock_recon.execute.call_args.kwargs
        assert isinstance(kwargs["tool_audit_logger"], SessionToolAuditLogger)
        # audit_logger was intentionally dropped in Task 3 (mirror whitebox)
        assert "audit_logger" not in kwargs
    finally:
        clear_audit_session()
        await session.close()
