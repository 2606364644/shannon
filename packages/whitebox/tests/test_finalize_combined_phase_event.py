"""Task 2 (组合扫描 D1): finalize_summary 组合分支调现有 log_phase_complete。

组合扫描模式(``combined=True``)下,白盒 finalize 结束的是「白盒阶段」而非整条 scan:
调现有 ``session.log_phase_complete("whitebox")``(写 ``PhaseEvent``,非 ``scan_end``),
留 scan 非终态,以便编排器(Task 4)在同目录追加黑盒阶段。

纯白盒(``combined=False``,默认)路径不变——仍调 ``log_workflow_complete(ws)`` 写终态。
**不新增事件类型、不覆盖 log_phase_complete 签名。**

mock 模式对齐 ``test_migration_activities.py::test_finalize_summary_logs_complete_and_clears_session``
(L88-118): ``MagicMock`` 绕 ``isinstance(session, NullAuditSession)`` guard +
patch ``get_audit_session`` / ``clear_audit_session`` / ``ensure_audit_session`` / ``stop_heartbeat``。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_session() -> MagicMock:
    """非 NullAuditSession 的 MagicMock —— 绕 finalize_summary 的 isinstance guard。"""
    mock_session = MagicMock()
    mock_session.log_workflow_complete = AsyncMock()
    mock_session.log_phase_complete = AsyncMock()
    mock_session.get_metrics = AsyncMock(return_value={
        "total_cost_usd": 0.0, "cost_currency": "USD",
    })
    return mock_session


def _summary_dict() -> dict:
    return {
        "status": "completed",
        "total_duration_ms": 100,
        "total_cost_usd": 0.0,
        "completed_agents": ["recon"],
        "agent_metrics": {},
        "error": None,
    }


@pytest.mark.asyncio
async def test_finalize_combined_true_calls_log_phase_complete(tmp_path):
    """combined=True → log_phase_complete("whitebox") 被调,log_workflow_complete 不被调。"""
    from supernova_whitebox.pipeline.activities import finalize_summary
    from supernova_whitebox.pipeline.shared import ActivityInput

    inp = ActivityInput(
        repo_path=str(tmp_path), workspace_path=str(tmp_path), combined=True,
    )
    mock_session = _make_mock_session()

    with patch("supernova_whitebox.pipeline.activities.ensure_audit_session", new=AsyncMock()) as mock_ensure, \
         patch("supernova_whitebox.pipeline.activities.stop_heartbeat", new=AsyncMock()), \
         patch("supernova_whitebox.audit.session_registry.get_audit_session", return_value=mock_session), \
         patch("supernova_whitebox.audit.session_registry.clear_audit_session") as mock_clear:
        await finalize_summary(inp, _summary_dict())

    mock_session.log_phase_complete.assert_awaited_once_with("whitebox")
    mock_session.log_workflow_complete.assert_not_awaited()
    mock_ensure.assert_awaited_once_with(inp)
    mock_clear.assert_called_once()


@pytest.mark.asyncio
async def test_finalize_combined_false_calls_log_workflow_complete(tmp_path):
    """combined=False (纯白盒, 默认) → log_workflow_complete 被调, log_phase_complete 不被调。

    回归守护: 纯白盒路径 byte-for-byte 不变。
    """
    from supernova_core.models.audit import WorkflowSummary
    from supernova_whitebox.pipeline.activities import finalize_summary
    from supernova_whitebox.pipeline.shared import ActivityInput

    inp = ActivityInput(
        repo_path=str(tmp_path), workspace_path=str(tmp_path), combined=False,
    )
    mock_session = _make_mock_session()

    with patch("supernova_whitebox.pipeline.activities.ensure_audit_session", new=AsyncMock()), \
         patch("supernova_whitebox.pipeline.activities.stop_heartbeat", new=AsyncMock()), \
         patch("supernova_whitebox.audit.session_registry.get_audit_session", return_value=mock_session), \
         patch("supernova_whitebox.audit.session_registry.clear_audit_session"):
        await finalize_summary(inp, _summary_dict())

    mock_session.log_workflow_complete.assert_awaited_once()
    mock_session.log_phase_complete.assert_not_awaited()
    ws = mock_session.log_workflow_complete.call_args[0][0]
    assert isinstance(ws, WorkflowSummary)
    assert ws.status == "completed"
