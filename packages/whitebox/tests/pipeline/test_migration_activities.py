"""C1 迁移 activity: setup_display 注入 AuditSession, run_heartbeat 长驻写文件, finalize_summary 写 scan_end + 清理.

These are WORKER-CONTAINER-PATH activities — the CLI run_scan does NOT call them
(CLI inlines AuditSession/HeartbeatManager itself). They exist so the Temporal
workflow (Task 4) can drive the same lifecycle via activities when running
inside a worker container (no TTY, event_file threaded from PipelineInput).
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.logging.log_bus import LogBus


@pytest.mark.asyncio
async def test_setup_display_injects_audit_session_with_event_file(tmp_path):
    """setup_display 构造 headless AuditSession(event_file 来自 input) 并 set_audit_session。

    event_file 透传到 WorkflowLogger.initialize → StructuredEventRenderer 挂载 →
    WorkflowHeader 落 events.ndjson（验证真行为，非 mock-theater）。
    """
    from shannon_whitebox.pipeline.activities import setup_display
    from shannon_whitebox.pipeline.shared import ActivityInput

    event_file = str(tmp_path / "events.ndjson")
    inp = ActivityInput(
        repo_path=str(tmp_path),
        workspace_path=str(tmp_path),
        event_file=event_file,
    )
    with patch("shannon_whitebox.audit.session_registry.set_audit_session") as mock_set:
        await setup_display(inp)
        mock_set.assert_called_once()
        session = mock_set.call_args[0][0]
        assert session is not None  # AuditSession 真构造了
        # event_file 透传: StructuredEventRenderer 挂载后 WorkflowHeader 落 ndjson
        assert Path(event_file).exists()
        await LogBus.drain_and_detach()  # cleanup: final flush + cancel drain task (paired with attach)
        await session.close()  # cleanup: 关 LogStream + StructuredEventRenderer 句柄


@pytest.mark.asyncio
async def test_setup_display_none_event_file_does_not_mount_renderer(tmp_path):
    """event_file=None 时不挂 StructuredEventRenderer（env 兜底由 WorkflowLogger 处理）。

    确保向后兼容: CLI 路径 event_file=None, 不产生 events.ndjson（靠 env SHANNON_WEB_EVENT_FILE）。
    """
    from shannon_whitebox.pipeline.activities import setup_display
    from shannon_whitebox.pipeline.shared import ActivityInput

    inp = ActivityInput(
        repo_path=str(tmp_path),
        workspace_path=str(tmp_path),
        event_file=None,
    )
    event_file = str(tmp_path / "events.ndjson")
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("SHANNON_WEB_EVENT_FILE", None)  # 确保 env 不兜底
        with patch("shannon_whitebox.audit.session_registry.set_audit_session") as mock_set:
            await setup_display(inp)
            session = mock_set.call_args[0][0]
            assert session is not None
            assert not Path(event_file).exists()  # event_file=None + env 未设 → 无 ndjson
            await LogBus.drain_and_detach()  # cleanup: final flush + cancel drain task (paired with attach)
            await session.close()


@pytest.mark.asyncio
async def test_run_heartbeat_writes_heartbeat_file_until_cancelled(tmp_path):
    """run_heartbeat 长驻写 heartbeat 文件; cancel 时干净退出(HeartbeatManager __aexit__)."""
    from shannon_whitebox.pipeline.activities import run_heartbeat
    from shannon_whitebox.pipeline.shared import ActivityInput

    inp = ActivityInput(repo_path=str(tmp_path), workspace_path=str(tmp_path))
    task = asyncio.create_task(run_heartbeat(inp))
    await asyncio.sleep(0.1)  # 让 heartbeat 初始写
    assert (tmp_path / "heartbeat").exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_finalize_summary_logs_complete_and_clears_session(tmp_path):
    """finalize_summary 构造 WorkflowSummary + 调 log_workflow_complete + clear_audit_session."""
    from shannon_whitebox.pipeline.activities import finalize_summary
    from shannon_whitebox.pipeline.shared import ActivityInput
    from shannon_core.models.audit import WorkflowSummary

    inp = ActivityInput(repo_path=str(tmp_path), workspace_path=str(tmp_path))
    mock_session = MagicMock()
    mock_session.log_workflow_complete = AsyncMock()
    summary = {
        "status": "completed",
        "total_duration_ms": 100,
        "total_cost_usd": 0.0,
        "completed_agents": ["recon"],
        "agent_metrics": {},
        "error": None,
    }
    with patch("shannon_whitebox.audit.session_registry.get_audit_session", return_value=mock_session), \
         patch("shannon_whitebox.audit.session_registry.clear_audit_session") as mock_clear:
        await finalize_summary(inp, summary)
        mock_session.log_workflow_complete.assert_awaited_once()
        # 验证传入的是真实 WorkflowSummary 且字段正确（非 mock-theater）
        ws = mock_session.log_workflow_complete.call_args[0][0]
        assert isinstance(ws, WorkflowSummary)
        assert ws.status == "completed"
        assert ws.completed_agents == ["recon"]
        assert ws.total_duration_ms == 100
        mock_clear.assert_called_once()
