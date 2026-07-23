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

from supernova_core.logging.log_bus import LogBus


@pytest.mark.asyncio
async def test_setup_display_injects_audit_session_with_event_file(tmp_path):
    """setup_display 构造 headless AuditSession(event_file 来自 input) 并 set_audit_session。

    event_file 透传到 WorkflowLogger.initialize → StructuredEventRenderer 挂载 →
    WorkflowHeader 落 events.ndjson（验证真行为，非 mock-theater）。
    """
    from supernova_whitebox.pipeline.activities import setup_display
    from supernova_whitebox.pipeline.shared import ActivityInput

    event_file = str(tmp_path / "events.ndjson")
    inp = ActivityInput(
        repo_path=str(tmp_path),
        workspace_path=str(tmp_path),
        event_file=event_file,
    )
    with patch("supernova_whitebox.audit.session_registry.set_audit_session") as mock_set:
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

    确保向后兼容: CLI 路径 event_file=None, 不产生 events.ndjson（靠 env SUPERNOVA_WEB_EVENT_FILE）。
    """
    from supernova_whitebox.pipeline.activities import setup_display
    from supernova_whitebox.pipeline.shared import ActivityInput

    inp = ActivityInput(
        repo_path=str(tmp_path),
        workspace_path=str(tmp_path),
        event_file=None,
    )
    event_file = str(tmp_path / "events.ndjson")
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("SUPERNOVA_WEB_EVENT_FILE", None)  # 确保 env 不兜底
        with patch("supernova_whitebox.audit.session_registry.set_audit_session") as mock_set:
            await setup_display(inp)
            session = mock_set.call_args[0][0]
            assert session is not None
            assert not Path(event_file).exists()  # event_file=None + env 未设 → 无 ndjson
            await LogBus.drain_and_detach()  # cleanup: final flush + cancel drain task (paired with attach)
            await session.close()


@pytest.mark.asyncio
async def test_heartbeat_started_and_stopped_cleanly(tmp_path):
    """start_heartbeat 写出首个 heartbeat; stop_heartbeat 干净停 daemon(join, 不卡/不残留).

    替代旧 run_heartbeat activity 测试: C1 Phase B 的 background activity 已删——worker
    max_concurrent_workflow_tasks=1(AuditSession 全局单例所致)下 worker 不 dispatch background
    activity handler → heartbeat 永不写(2026-07-23 hr_1784788700 回归). 改 setup_display 启动
    HeartbeatManager daemon 线程, finalize_summary 调 stop_heartbeat 停.
    """
    from supernova_core.runtime.heartbeat import start_heartbeat, stop_heartbeat

    await start_heartbeat(tmp_path)
    assert (tmp_path / "heartbeat").exists()  # __aenter__ 同步写首个
    await stop_heartbeat()  # daemon join + 清理, 应快速返回


@pytest.mark.asyncio
async def test_finalize_summary_logs_complete_and_clears_session(tmp_path):
    """finalize_summary 构造 WorkflowSummary + 调 log_workflow_complete + clear_audit_session."""
    from supernova_whitebox.pipeline.activities import finalize_summary
    from supernova_whitebox.pipeline.shared import ActivityInput
    from supernova_core.models.audit import WorkflowSummary

    inp = ActivityInput(repo_path=str(tmp_path), workspace_path=str(tmp_path))
    mock_session = MagicMock()
    mock_session.log_workflow_complete = AsyncMock()
    mock_session.get_metrics = AsyncMock(return_value={
        "total_cost_usd": 0.0, "cost_currency": "USD",
    })
    summary = {
        "status": "completed",
        "total_duration_ms": 100,
        "total_cost_usd": 0.0,
        "completed_agents": ["recon"],
        "agent_metrics": {},
        "error": None,
    }
    with patch("supernova_whitebox.audit.session_registry.get_audit_session", return_value=mock_session), \
         patch("supernova_whitebox.audit.session_registry.clear_audit_session") as mock_clear:
        await finalize_summary(inp, summary)
        mock_session.log_workflow_complete.assert_awaited_once()
        # 验证传入的是真实 WorkflowSummary 且字段正确（非 mock-theater）
        ws = mock_session.log_workflow_complete.call_args[0][0]
        assert isinstance(ws, WorkflowSummary)
        assert ws.status == "completed"
        assert ws.completed_agents == ["recon"]
        assert ws.total_duration_ms == 100
        mock_clear.assert_called_once()


@pytest.mark.asyncio
async def test_finalize_summary_reads_cost_from_session_metrics(tmp_path):
    """finalize_summary cost/currency 从 session.get_metrics() 读(完整),非 summary dict。

    summary dict 的 total_cost 来自 PipelineState.agent_metrics(workflow self._state 构建),
    LLM 轨关闭时残缺→0(与 NodeGoat CLI ``Total Cost: $0.0000`` 回归同源)。对齐 CLI 路径
    (worker._build_final_summary):两条路径 cost 数据源一致 = MetricsTracker。"""
    from supernova_whitebox.pipeline.activities import finalize_summary
    from supernova_whitebox.pipeline.shared import ActivityInput
    from supernova_core.models.audit import WorkflowSummary

    inp = ActivityInput(repo_path=str(tmp_path), workspace_path=str(tmp_path))
    mock_session = MagicMock()
    mock_session.log_workflow_complete = AsyncMock()
    mock_session.get_metrics = AsyncMock(return_value={
        "total_cost_usd": 6.49, "cost_currency": "CNY",
    })
    # summary dict 的 total_cost_usd 残缺为 0(LLM 轨关),应被 session metrics 覆盖
    summary = {"status": "completed", "total_duration_ms": 100, "total_cost_usd": 0.0,
               "completed_agents": [], "agent_metrics": {}, "error": None}
    with patch("supernova_whitebox.audit.session_registry.get_audit_session", return_value=mock_session), \
         patch("supernova_whitebox.audit.session_registry.clear_audit_session"):
        await finalize_summary(inp, summary)

    ws = mock_session.log_workflow_complete.call_args[0][0]
    assert isinstance(ws, WorkflowSummary)
    assert ws.total_cost_usd == pytest.approx(6.49)   # 非 summary dict 的 0
    assert ws.cost_currency == "CNY"


@pytest.mark.asyncio
async def test_setup_display_mounts_logbus_handler_so_logevent_reaches_files(tmp_path):
    """裂痕四修复: setup_display 调 configure_logging 挂 LogBusHandler, 之后散落
    getLogger 的 LogEvent 经 LogBus→dispatcher 进 events.ndjson + diagnostic.log。
    （修前: worker 路径 root 无 LogBusHandler → LogEvent 走 lastResort stderr, 两文件皆空。）"""
    import asyncio, logging
    from supernova_whitebox.pipeline.activities import setup_display
    from supernova_whitebox.pipeline.shared import ActivityInput
    from supernova_core.audit.session_registry import clear_audit_session
    from supernova_core.logging.log_bus import LogBus

    # 快照 + 还原 root logger（configure_logging 是进程级）
    root = logging.getLogger()
    saved_handlers = list(root.handlers)

    event_file = str(tmp_path / "events.ndjson")
    inp = ActivityInput(
        repo_path=str(tmp_path),
        workspace_path=str(tmp_path),
        workspace_name=tmp_path.name,
        event_file=event_file,
    )
    try:
        await setup_display(inp)
        logging.getLogger("shannon_test_diag").warning("diag-from-activity")
        await asyncio.sleep(0.3)  # 让 LogBus drain task 把 LogEvent dispatch 落盘
    finally:
        await LogBus.drain_and_detach()
        clear_audit_session()
        root.handlers = saved_handlers  # 还原，避免泄漏到其它测试

    ndjson = (tmp_path / "events.ndjson").read_text("utf-8")
    assert '"type": "LogEvent"' in ndjson, ndjson
    assert "diag-from-activity" in ndjson
    diag = (tmp_path / "logs" / "diagnostic.log").read_text("utf-8")
    assert "diag-from-activity" in diag, diag
