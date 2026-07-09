"""scan_runner 单元测试：ShutdownController / poll_progress / run_scan_graceful。"""

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.runtime.scan_runner import (
    ScanCancelled,
    ShutdownController,
    await_workflow_with_shutdown,
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


class TestShutdownMessagesUsePrintLine:
    """spec 组件 4：取消消息经 print_line，格式对齐 [timestamp] [SCAN/CANCEL] symbol body。"""

    def test_graceful_message_has_tagged_format(self, capsys):
        ctrl = ShutdownController()
        ctrl._loop = MagicMock()
        ctrl._on_signal(signal.SIGINT)  # 触发 _trigger_graceful
        out = capsys.readouterr().out
        # 不再是裸 "正在优雅取消…"，而是 [timestamp] [SCAN  ] ... 带等宽标签
        assert "[SCAN" in out and "] 正在优雅取消" in out, out

    def test_sigterm_graceful_message_tagged(self, capsys):
        ctrl = ShutdownController()
        ctrl._loop = MagicMock()
        ctrl._on_signal(signal.SIGTERM)
        out = capsys.readouterr().out
        assert "[SCAN" in out and "正在优雅取消" in out, out

    @pytest.mark.asyncio
    async def test_do_cancel_message_tagged(self, capsys):
        """_do_cancel 的 '正在取消 Temporal workflow…' 经 print_line 带标签。"""
        from shannon_core.runtime.scan_runner import _do_cancel
        result_task = asyncio.ensure_future(asyncio.sleep(100))  # 永不自然完成
        try:
            fake_handle = MagicMock()
            fake_handle.cancel = AsyncMock()
            # _do_cancel 内部吞 TimeoutError（spec 设计：超时放弃等待不 escalate）
            await _do_cancel(fake_handle, result_task, cancel_grace_seconds=0.01)
        finally:
            result_task.cancel()
        out = capsys.readouterr().out
        assert "[CANCEL" in out and "正在取消 Temporal workflow" in out, out


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


class TestAwaitWorkflowWithShutdown:
    @pytest.mark.asyncio
    async def test_no_poll_when_progress_type_is_none(self):
        fake_handle = AsyncMock()
        fake_handle.result = AsyncMock(return_value={"status": "completed"})
        fake_handle.query = AsyncMock()
        ctrl = ShutdownController()  # not set

        result = await await_workflow_with_shutdown(fake_handle, ctrl, progress_type=None)

        assert result == {"status": "completed"}
        fake_handle.query.assert_not_awaited()  # 无 poll → 不查询进度

    @pytest.mark.asyncio
    async def test_poll_started_when_progress_type_given(self):
        fake_handle = AsyncMock()
        fake_handle.result = AsyncMock(return_value={"status": "completed"})

        async def fake_sleep(seconds):
            raise asyncio.CancelledError()  # poll 第一轮 sleep 即取消

        ctrl = ShutdownController()
        with patch("shannon_core.runtime.scan_runner.asyncio.sleep", fake_sleep):
            result = await await_workflow_with_shutdown(
                fake_handle, ctrl, progress_type=MagicMock(), total=7
            )

        assert result == {"status": "completed"}
        fake_handle.query.assert_awaited_once()  # poll 跑了一轮查询

    @pytest.mark.asyncio
    async def test_raises_scan_cancelled_when_shutdown_triggered(self):
        fake_handle = AsyncMock()
        # result 永不自然完成 → 必须靠取消路径
        fake_handle.result = MagicMock(
            return_value=asyncio.get_running_loop().create_future()
        )
        fake_handle.cancel = AsyncMock()

        triggered = ShutdownController()
        triggered._event.set()  # 预置：中断已发生

        with pytest.raises(ScanCancelled):
            await await_workflow_with_shutdown(
                fake_handle, triggered, cancel_grace_seconds=0.01
            )

        fake_handle.cancel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_passes_ctrl_cleanup_callback_to_do_cancel(self):
        """路径② 接通: await_workflow_with_shutdown 把 ctrl.cleanup_callback 传给 _do_cancel。"""
        fake_handle = AsyncMock()
        # result 永不自然完成 -> 必须靠取消路径
        fake_handle.result = MagicMock(
            return_value=asyncio.get_running_loop().create_future()
        )
        fake_handle.cancel = AsyncMock()

        triggered = ShutdownController()
        triggered._event.set()  # 预置：中断已发生

        def cleanup(session_ids=None):
            pass

        triggered.install(MagicMock(), cleanup_callback=cleanup)

        captured = {}

        async def fake_do_cancel(handle, result_task, grace, *, cleanup_callback=None):
            captured["cb"] = cleanup_callback

        with patch("shannon_core.runtime.scan_runner._do_cancel", fake_do_cancel):
            with pytest.raises(ScanCancelled):
                await await_workflow_with_shutdown(
                    fake_handle, triggered, cancel_grace_seconds=0.01
                )

        assert captured["cb"] is cleanup  # ctrl 的 callback 被传给 _do_cancel


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


class TestShutdownCleanup:
    """路径③: _force_exit 前 os._exit 必有同步进程清理。"""

    def test_force_exit_calls_cleanup_before_os_exit(self):
        """第 2 次 SIGINT -> _force_exit -> 先调 cleanup_callback 再 os._exit(130)。"""
        ctrl = ShutdownController()
        called = []

        def cleanup(session_ids=None):
            called.append(session_ids)

        ctrl._loop = MagicMock()
        ctrl.install(MagicMock(), cleanup_callback=cleanup)
        ctrl._on_signal(signal.SIGINT)  # 第 1 次: graceful
        with patch("shannon_core.runtime.scan_runner.os._exit") as mock_exit:
            ctrl._on_signal(signal.SIGINT)  # 第 2 次: force
        mock_exit.assert_called_once_with(130)
        assert called == [None]  # cleanup 在 os._exit 前被调一次,session_ids=None

    def test_force_exit_without_callback_still_exits(self):
        """未提供 cleanup_callback 时 _force_exit 仍正常 os._exit(不崩)。"""
        ctrl = ShutdownController()
        ctrl._loop = MagicMock()
        ctrl.install(MagicMock())  # 无 cleanup_callback
        ctrl._on_signal(signal.SIGINT)
        with patch("shannon_core.runtime.scan_runner.os._exit") as mock_exit:
            ctrl._on_signal(signal.SIGINT)
        mock_exit.assert_called_once_with(130)

    def test_cleanup_exception_does_not_block_exit(self):
        """cleanup_callback 抛异常时仍必须 os._exit(清理绝不阻塞退出)。"""
        ctrl = ShutdownController()

        def boom(session_ids=None):
            raise RuntimeError("cleanup blew up")

        ctrl._loop = MagicMock()
        ctrl.install(MagicMock(), cleanup_callback=boom)
        ctrl._on_signal(signal.SIGINT)
        with patch("shannon_core.runtime.scan_runner.os._exit") as mock_exit:
            ctrl._on_signal(signal.SIGINT)
        mock_exit.assert_called_once_with(130)

    async def test_do_cancel_calls_cleanup_on_timeout(self):
        """路径②: _do_cancel grace 超时后调 cleanup_callback。"""
        from shannon_core.runtime.scan_runner import _do_cancel

        called = []

        def cleanup(session_ids=None):
            called.append(session_ids)

        fake_handle = MagicMock()
        fake_handle.cancel = AsyncMock()
        result_task = asyncio.ensure_future(asyncio.sleep(100))  # 永不完成 -> 超时
        try:
            await _do_cancel(
                fake_handle, result_task, cancel_grace_seconds=0.01,
                cleanup_callback=cleanup,
            )
        except Exception:
            pass
        assert called == [None]
        result_task.cancel()
