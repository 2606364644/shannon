"""Task 2 (组合扫描 D1): finalize_summary 组合分支调现有 log_phase_complete。

组合扫描模式(``combined=True``)下,白盒 finalize 结束的是「白盒阶段」而非整条 scan:
调现有 ``session.log_phase_complete("whitebox")``(写 ``PhaseEvent``,非 ``scan_end``),
留 scan 非终态,以便编排器(Task 4)在同目录追加黑盒阶段。

纯白盒(``combined=False``,默认)路径不变——仍调 ``log_workflow_complete(ws)`` 写终态。
**不新增事件类型、不覆盖 log_phase_complete 签名。**

mock 模式对齐 ``test_migration_activities.py::test_finalize_summary_logs_complete_and_clears_session``
(L88-118): ``MagicMock`` 绕 ``isinstance(session, NullAuditSession)`` guard +
patch ``get_audit_session`` / ``clear_audit_session`` / ``ensure_audit_session`` / ``stop_heartbeat``。

drain-task 收尾回归（2026-08-18 NodeGoat 真机）:combined 分支历史版只写 phase 事件不
close session,``clear_audit_session()`` 随即摘走 ``_SESSIONS`` 最后引用 → dispatcher
drain task 成孤儿(纯 PENDING 挂 ``queue.get()``)→ 下个 scan 期间被 GC 销毁,
``Task was destroyed but it is pending!`` 经 LogBus 误路由进当时活跃 scan 的 live 页。
黑盒阶段本就自建新 session(blackbox setup_display),白盒 session 在 combined finalize
后无人复用 → 必须就地 close。见 ``test_finalize_combined_closes_session``。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_session() -> MagicMock:
    """非 NullAuditSession 的 MagicMock —— 绕 finalize_summary 的 isinstance guard。"""
    mock_session = MagicMock()
    mock_session.log_workflow_complete = AsyncMock()
    mock_session.log_phase_complete = AsyncMock()
    # 真实 AuditSession.close 是 async（combined 分支 2026-08-18 起调用）。
    mock_session.close = AsyncMock()
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
async def test_finalize_combined_closes_session(tmp_path):
    """combined=True → finalize 后 session 必须 close（drain task 不残留 PENDING）。

    真实 AuditSession（非 MagicMock——mock 的 close 永远"被调"，测不出 drain task
    泄漏），断言：finalize 返回后 dispatcher 已收尾（_drain_task is None），且事件
    已被消费（PhaseEvent 落盘 → queue join 语义由 close 内部保证）。
    """
    from supernova_core.audit.session import AuditSession
    from supernova_core.models.metrics import SessionMetadata
    from supernova_whitebox.pipeline.activities import finalize_summary
    from supernova_whitebox.pipeline.shared import ActivityInput

    meta = SessionMetadata(
        id="wb-fixture", web_url=None, repo_path=str(tmp_path),
        output_path=str(tmp_path.parent),
    )
    session = AuditSession(meta, use_rich=False, console=None)
    await session.initialize(workflow_id="wb-fixture")
    # close 前 hold 引用：close 后 session.dispatcher 为 None（log_* 全 no-op），
    # 断言 drain task 必须对 finalize 前创建的 dispatcher 生效。
    dispatcher = session.dispatcher
    assert dispatcher is not None and dispatcher._drain_task is not None
    inp = ActivityInput(
        repo_path=str(tmp_path), workspace_path=str(tmp_path), combined=True,
    )

    with patch("supernova_whitebox.pipeline.activities.ensure_audit_session", new=AsyncMock()), \
         patch("supernova_whitebox.pipeline.activities.stop_heartbeat", new=AsyncMock()), \
         patch("supernova_whitebox.audit.session_registry.get_audit_session", return_value=session), \
         patch("supernova_whitebox.audit.session_registry.clear_audit_session"):
        await finalize_summary(inp, _summary_dict())

    assert dispatcher._drain_task is None, (
        "combined finalize 后 dispatcher drain task 仍存活——clear_audit_session 将摘走"
        "最后引用,孤儿 task 会被 GC 销毁并误报 ERROR 到下个 scan 的 live 页")


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
