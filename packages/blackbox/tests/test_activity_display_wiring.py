"""L3: tool/llm events through SessionToolAuditLogger reach workflow.log via the
blackbox AuditSession -> WorkflowLogger -> dispatcher -> FileLogRenderer path."""
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from supernova_core.models.audit import AgentEndResult
from supernova_core.models.metrics import SessionMetadata
from supernova_core.models.errors import PentestError
from supernova_core.audit.session import AuditSession
from supernova_core.audit.session_registry import set_audit_session, clear_audit_session
from supernova_core.audit.session_tool_audit_logger import SessionToolAuditLogger
from supernova_core.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="s1", web_url="https://example.com", output_path=str(tmp_path))


async def test_session_tool_audit_logger_feeds_workflow_log(tmp_path: Path):
    session = AuditSession(_make_meta(tmp_path))
    await session.initialize()
    set_audit_session(session)
    try:
        await session.start_agent("injection-exploit", "p", attempt=1)
        lg = SessionToolAuditLogger(session, "injection-exploit", attempt=1)
        await lg.initialize()
        await lg.log_tool_start("Bash", {"command": "curl 'http://x/?q=<script>'"})
        await lg.log_assistant_turn(1, "confirmed reflected XSS")
        await lg.close(success=True, duration_ms=100)
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


async def test_run_exploit_agent_failure_records_cost_from_pentest_error(tmp_path, monkeypatch):
    """L3: exploit agent 失败（except PentestError）时，end_agent 从 PentestError.context
    取 executor 携带的 cost——失败 agent 也记真实消耗（修 error path cost 归 0）。"""
    from temporalio.exceptions import ApplicationError as ApplicationFailure

    from supernova_blackbox.pipeline import activities as act_mod
    from supernova_blackbox.pipeline.shared import BlackboxActivityInput

    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    # 固定 workflow_id，让 set/get_audit_session 解析到同一 key（否则 NullAuditSession no-op）。
    monkeypatch.setattr("temporalio.activity.info",
                        lambda: MagicMock(attempt=1, workflow_id="test-wf"))
    set_audit_session(session)
    end_spy = AsyncMock()
    monkeypatch.setattr(session, "end_agent", end_spy)
    try:
        fake_exploit = MagicMock()
        fake_exploit.execute = AsyncMock(side_effect=PentestError(
            "boom", "validation",
            context={"cost_usd": 0.42, "cost_currency": "CNY", "model": "glm-5.2",
                     "num_turns": 2, "input_tokens": 80, "output_tokens": 40}))
        # run_exploit_agent 函数内 import ExploitExecutor，patch 源类使 import 拿到 fake。
        with patch("supernova_blackbox.agents.exploit_executor.ExploitExecutor",
                   return_value=fake_exploit):
            with pytest.raises(ApplicationFailure):
                await act_mod.run_exploit_agent(BlackboxActivityInput(
                    web_url="https://x", vuln_type="injection", workspace_path=str(tmp_path)))
        assert end_spy.called
        result = end_spy.call_args.args[1]  # end_agent(name, AgentEndResult)
        assert result.success is False
        assert result.cost_usd == 0.42
        assert result.cost_currency == "CNY"
        assert result.model == "glm-5.2"
        assert result.num_turns == 2
        assert result.input_tokens == 80
    finally:
        clear_audit_session()
        await session.close()
