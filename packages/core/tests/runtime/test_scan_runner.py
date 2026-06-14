"""scan_runner 单元测试：ShutdownController / poll_progress / run_scan_graceful。"""

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.runtime.scan_runner import (
    ScanCancelled,
    ShutdownController,
    poll_progress,
    run_scan_graceful,
)


class TestShutdownController:
    def test_first_sigint_triggers_graceful(self):
        ctrl = ShutdownController()
        ctrl._loop = MagicMock()
        ctrl._on_signal(signal.SIGINT)
        assert ctrl.is_set() is True
        assert ctrl._count == 1

    def test_second_sigint_force_exits_130(self):
        ctrl = ShutdownController()
        ctrl._loop = MagicMock()
        ctrl._on_signal(signal.SIGINT)  # 第 1 次
        with patch("shannon_core.runtime.scan_runner.os._exit") as mock_exit:
            ctrl._on_signal(signal.SIGINT)  # 第 2 次
        mock_exit.assert_called_once_with(130)

    def test_sigterm_triggers_graceful_without_counting(self):
        ctrl = ShutdownController()
        ctrl._loop = MagicMock()
        ctrl._on_signal(signal.SIGTERM)
        assert ctrl.is_set() is True
        assert ctrl._count == 0  # SIGTERM 不参与双击计数

    def test_repeated_graceful_only_sets_event_once(self):
        ctrl = ShutdownController()
        ctrl._loop = MagicMock()
        ctrl._on_signal(signal.SIGINT)
        ctrl._on_signal(signal.SIGTERM)  # 已 set，不重复
        assert ctrl._count == 1

    def test_install_registers_sigint_and_sigterm(self):
        ctrl = ShutdownController()
        loop = MagicMock()
        ctrl.install(loop)
        registered = {call.args[0] for call in loop.add_signal_handler.call_args_list}
        assert registered == {signal.SIGINT, signal.SIGTERM}

    def test_uninstall_removes_handlers(self):
        ctrl = ShutdownController()
        loop = MagicMock()
        ctrl.install(loop)
        ctrl.uninstall()
        removed = {call.args[0] for call in loop.remove_signal_handler.call_args_list}
        assert removed == {signal.SIGINT, signal.SIGTERM}

    @pytest.mark.asyncio
    async def test_wait_returns_after_event_set(self):
        ctrl = ShutdownController()
        ctrl._loop = MagicMock()
        ctrl._on_signal(signal.SIGINT)  # set event
        await asyncio.wait_for(ctrl.wait(), timeout=1.0)  # 立即返回


class TestPollProgress:
    @pytest.mark.asyncio
    async def test_queries_and_prints_one_iteration(self, capsys):
        fake_handle = AsyncMock()
        progress = MagicMock(
            elapsed_ms=30000,
            current_phase="scan",
            current_agent="agent1",
            completed_agents=["a", "b"],
        )
        fake_handle.query = AsyncMock(return_value=progress)

        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 1:
                raise asyncio.CancelledError()

        with patch("shannon_core.runtime.scan_runner.asyncio.sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await poll_progress(fake_handle, progress_type=MagicMock(), total=13)

        # 注入的 progress_type 必须被原样作为 result_type 传给 query
        assert fake_handle.query.await_args.args == ("PipelineProgress",)
        assert fake_handle.query.await_args.kwargs.get("result_type") is not None
        out = capsys.readouterr().out
        assert "[30s] Phase: scan | Agent: agent1 | Completed: 2/13" in out

    @pytest.mark.asyncio
    async def test_swallows_query_exception_and_continues(self):
        fake_handle = AsyncMock()
        fake_handle.query = AsyncMock(side_effect=RuntimeError("workflow gone"))

        async def fake_sleep(seconds):
            raise asyncio.CancelledError()

        with patch("shannon_core.runtime.scan_runner.asyncio.sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await poll_progress(fake_handle, progress_type=MagicMock(), total=13)
        # 异常被吞掉，没有向上抛 RuntimeError


def _make_fake_worker():
    """构造可作 async context manager 的 fake Worker。"""
    fake = AsyncMock()
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=False)
    return fake


class TestRunScanGracefulNormal:
    @pytest.mark.asyncio
    async def test_normal_completion_returns_result_without_cancel(self):
        fake_handle = AsyncMock()
        fake_handle.result = AsyncMock(return_value={"status": "completed", "vulns": 3})
        fake_handle.cancel = AsyncMock()
        fake_handle.query = AsyncMock()

        fake_client = AsyncMock()
        fake_client.start_workflow = AsyncMock(return_value=fake_handle)

        fake_worker = _make_fake_worker()

        with patch(
            "shannon_core.runtime.scan_runner.Client.connect",
            AsyncMock(return_value=fake_client),
        ), patch(
            "shannon_core.runtime.scan_runner.Worker", return_value=fake_worker
        ), patch(
            "shannon_core.runtime.scan_runner.generate_task_queue",
            return_value="tq-test",
        ), patch.object(ShutdownController, "install"), patch.object(
            ShutdownController, "uninstall"
        ):  # 不注册真实信号 handler，保持测试纯净
            result = await run_scan_graceful(
                temporal_address="localhost:7233",
                task_queue_prefix="x",
                workflow_cls=MagicMock(),
                workflow_input=MagicMock(workspace_name="ws1"),
                activities=[],
                progress_type=MagicMock(),
            )

        assert result == {"status": "completed", "vulns": 3}
        fake_handle.cancel.assert_not_awaited()  # 正常完成不取消


class TestRunScanGracefulCancel:
    @pytest.mark.asyncio
    async def test_shutdown_triggers_cancel_and_raises_scan_cancelled(self):
        fake_handle = AsyncMock()
        # result 永不自然完成 → 模拟必须靠 cancel
        fake_handle.result = MagicMock(
            return_value=asyncio.get_running_loop().create_future()
        )
        fake_handle.cancel = AsyncMock()

        fake_client = AsyncMock()
        fake_client.start_workflow = AsyncMock(return_value=fake_handle)
        fake_worker = _make_fake_worker()

        triggered = ShutdownController()
        triggered._event.set()  # 预置：中断已发生
        triggered.install = MagicMock()   # 不注册真实信号 handler
        triggered.uninstall = MagicMock()

        with patch(
            "shannon_core.runtime.scan_runner.Client.connect",
            AsyncMock(return_value=fake_client),
        ), patch(
            "shannon_core.runtime.scan_runner.Worker", return_value=fake_worker
        ), patch(
            "shannon_core.runtime.scan_runner.generate_task_queue",
            return_value="tq-test",
        ), patch(
            "shannon_core.runtime.scan_runner.ShutdownController",
            return_value=triggered,
        ):
            with pytest.raises(ScanCancelled):
                await run_scan_graceful(
                    temporal_address="localhost:7233",
                    task_queue_prefix="x",
                    workflow_cls=MagicMock(),
                    workflow_input=MagicMock(workspace_name="ws1"),
                    activities=[],
                    progress_type=MagicMock(),
                    cancel_grace_seconds=0.01,  # 立即超时
                )

        fake_handle.cancel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_exception_still_raises_scan_cancelled(self):
        fake_handle = AsyncMock()
        fake_handle.result = MagicMock(
            return_value=asyncio.get_running_loop().create_future()
        )
        fake_handle.cancel = AsyncMock(side_effect=RuntimeError("server unreachable"))

        fake_client = AsyncMock()
        fake_client.start_workflow = AsyncMock(return_value=fake_handle)
        fake_worker = _make_fake_worker()

        triggered = ShutdownController()
        triggered._event.set()
        triggered.install = MagicMock()
        triggered.uninstall = MagicMock()

        with patch(
            "shannon_core.runtime.scan_runner.Client.connect",
            AsyncMock(return_value=fake_client),
        ), patch(
            "shannon_core.runtime.scan_runner.Worker", return_value=fake_worker
        ), patch(
            "shannon_core.runtime.scan_runner.generate_task_queue",
            return_value="tq-test",
        ), patch(
            "shannon_core.runtime.scan_runner.ShutdownController",
            return_value=triggered,
        ):
            with pytest.raises(ScanCancelled):  # 不是 RuntimeError
                await run_scan_graceful(
                    temporal_address="localhost:7233",
                    task_queue_prefix="x",
                    workflow_cls=MagicMock(),
                    workflow_input=MagicMock(workspace_name="ws1"),
                    activities=[],
                    progress_type=MagicMock(),
                    cancel_grace_seconds=0.01,
                )
