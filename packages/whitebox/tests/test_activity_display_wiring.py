from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from shannon_core.models.metrics import SessionMetadata
from shannon_core.models.audit import AgentEndResult
from shannon_core.models.errors import PentestError
from shannon_whitebox.audit.session import AuditSession
from shannon_whitebox.audit.session_registry import set_audit_session, clear_audit_session
from shannon_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
from shannon_whitebox.audit.utils import generate_audit_path
from shannon_whitebox.pipeline.shared import ActivityInput


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="s1", web_url="https://example.com", output_path=str(tmp_path))


async def test_session_tool_audit_logger_feeds_workflow_log(tmp_path: Path):
    """L3: tool/llm events through SessionToolAuditLogger reach workflow.log
    via AuditSession -> WorkflowLogger -> dispatcher -> FileLogRenderer."""
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    set_audit_session(session)
    try:
        await session.start_agent("injection-vuln", "p", attempt=1)
        lg = SessionToolAuditLogger(session, "injection-vuln", attempt=1)
        await lg.initialize()
        await lg.log_tool_start("Bash", {"command": "rg -n eval"})
        await lg.log_assistant_turn(1, "found sinks")
        await lg.close(success=True, duration_ms=100)
        await session.end_agent("injection-vuln", AgentEndResult(
            success=True, duration_ms=100, cost_usd=0.01, attempt_number=1))
    finally:
        clear_audit_session()
        await session.close()
    wf = (generate_audit_path(_make_meta(tmp_path)) / "workflow.log").read_text()
    assert "[AGENT] [Injection] injection-vuln: Starting" in wf
    assert "[TOOL]  [Injection] injection-vuln: Bash:" in wf
    assert "[LLM]   [Injection] injection-vuln: Turn 1:" in wf
    assert "[AGENT] [Injection] injection-vuln: Completed" in wf


async def test_run_agent_failure_path_logs_end_and_error(tmp_path: Path, monkeypatch):
    """run_agent's except branch must call end_agent(failed) + log_error before
    re-raising ApplicationFailure. Exercises the failure state-machine ordering."""
    from temporalio.exceptions import ApplicationError as ApplicationFailure

    meta = SessionMetadata(id="s1", web_url="https://example.com", output_path=str(tmp_path))
    session = AuditSession(meta)
    await session.initialize()
    set_audit_session(session)
    try:
        # Force activity.info().attempt = 1 without a real Temporal context
        monkeypatch.setattr("temporalio.activity.info", lambda: MagicMock(attempt=1))
        # Stub AgentExecutor.execute to raise a PentestError
        from shannon_whitebox.pipeline import activities as act_mod
        fake_executor = MagicMock()
        fake_executor.execute = AsyncMock(side_effect=PentestError("boom", "agent"))
        with patch.object(act_mod, "AgentExecutor", return_value=fake_executor):
            with pytest.raises(ApplicationFailure):
                await act_mod.run_agent(ActivityInput(
                    repo_path=str(tmp_path), workspace_name="injection-vuln"))
        # The failure path must have logged end_agent(failed) + error to workflow.log
        wf = (generate_audit_path(meta) / "workflow.log").read_text()
        assert "[Injection] injection-vuln" in wf  # agent row touched
        assert "boom" in wf  # error surfaced
    finally:
        clear_audit_session()
        await session.close()


async def test_step_intent_flows_end_to_end_from_registry(tmp_path: Path):
    """Seam test: a step intent sourced from the REAL step_intents.intent_for
    registry flows through AuditSession.track_step -> WorkflowLogger.log_step ->
    real FileLogRenderer into workflow.log, rendered verbatim on a [STEP] line.

    No hand-written literal: the asserted string is intent_for("adjudication")'s
    actual registry value. Would fail if the registry changed it or if any layer
    in the chain (track_step / log_step / StepEvent.intent / FileLogRenderer._step)
    dropped the intent.
    """
    from shannon_whitebox.pipeline.step_intents import intent_for

    intent = intent_for("adjudication")
    assert intent is not None  # guard: registry must know this step

    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    try:
        async with session.track_step("pre-recon", "adjudication", intent=intent):
            pass
    finally:
        await session.close()

    wf = (generate_audit_path(meta) / "workflow.log").read_text()
    # FileLogRenderer._step renders intent as " — {intent}" on the [STEP] line.
    assert f"[STEP] adjudication:" in wf
    assert f" — {intent}" in wf
