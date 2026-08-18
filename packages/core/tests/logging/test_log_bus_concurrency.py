"""P3c 阶段 3：LogBus 按 workflow_id 隔离（A 的事件不 dispatch 到 B）。

设计：root 挂单个 LogBusHandler 共享多 workflow；emit 时按 _resolve_wf_id()
动态路由到对应 bus（async/to_thread 线程 contextvar 传播 → 正确 workflow_id）。
LogBus 代理（__getattr__）让现有 LogBus.attach / LogBus.queue 调用零改动。
"""
import asyncio
import logging

import pytest
from unittest.mock import AsyncMock, MagicMock

from supernova_core.display.events import LogEvent
from supernova_core.logging.log_bus import (
    get_log_bus,
    attach,
    drain_and_detach,
    is_attached,
    _BUSES,
    LogBusHandler,
)


@pytest.fixture(autouse=True)
async def _restore_buses():
    yield
    for bus in list(_BUSES.values()):
        bus._attached = False
        bus._dispatcher = None
        if bus._drain_task is not None and not bus._drain_task.done():
            bus._drain_task.cancel()
        bus._drain_task = None
        while True:
            try:
                bus.queue.get_nowait()
            except Exception:
                break
    _BUSES.clear()
    from supernova_core.logging.log_bus import reset_diagnostic
    reset_diagnostic()  # diagnostic 是进程级单例（不在 bus 上），单独重置


def _spy():
    s = MagicMock()
    s.dispatch = AsyncMock()  # DisplayDispatcher.add() sync; dispatch async
    return s


@pytest.mark.asyncio
async def test_two_buses_dispatch_to_own_dispatcher():
    """wf-A 的 LogEvent 只 dispatch 到 wf-A 的 dispatcher，不串到 B。"""
    spyA, spyB = _spy(), _spy()
    await attach(spyA, workflow_id="wf-A")
    await attach(spyB, workflow_id="wf-B")
    busA = get_log_bus("wf-A")
    busA.queue.put_nowait(
        LogEvent(
            timestamp="t",
            category="WARNING",
            logger_name="x",
            level="WARNING",
            message="from-A",
            exc_txt=None,
        )
    )
    await asyncio.sleep(0.15)  # 等 drain
    await drain_and_detach(workflow_id="wf-A")
    await drain_and_detach(workflow_id="wf-B")
    spyA.dispatch.assert_awaited()
    spyB.dispatch.assert_not_awaited()  # B 完全没收到 A 的事件


@pytest.mark.asyncio
async def test_detach_one_does_not_cancel_other_drain():
    """wf-A detach 不 cancel wf-B 的 drain task。"""
    spyA, spyB = _spy(), _spy()
    await attach(spyA, workflow_id="wf-A")
    await attach(spyB, workflow_id="wf-B")
    await drain_and_detach(workflow_id="wf-A")
    busB = get_log_bus("wf-B")
    assert busB._drain_task is not None
    assert not busB._drain_task.done()
    await drain_and_detach(workflow_id="wf-B")


@pytest.mark.asyncio
async def test_is_attached_isolated_per_workflow():
    """is_attached 按 workflow 隔离：A attach 不影响 B 的 attached 状态。"""
    spy = _spy()
    assert not is_attached(workflow_id="wf-A")
    assert not is_attached(workflow_id="wf-B")
    await attach(spy, workflow_id="wf-A")
    assert is_attached(workflow_id="wf-A")
    assert not is_attached(workflow_id="wf-B")  # B 仍未 attach
    await drain_and_detach(workflow_id="wf-A")


@pytest.mark.asyncio
async def test_drain_and_detach_pops_bus_and_proxy_routes_module_fn(monkeypatch):
    """收尾后 bus 不残留（2026-08-18）：

    - 模块级 ``drain_and_detach`` 从 ``_BUSES`` pop（``setdefault`` 只增不减 ->
      每 workflow 永久残留一个 bus + diagnostic 句柄）；
    - ``LogBus.drain_and_detach()``（proxy 属性访问）必须走模块级函数而非转发
      实例方法，否则 pop 被绕过（finalize_summary 走的就是 proxy 路径）。
    """
    from supernova_core.logging.log_bus import LogBus

    spy = _spy()
    await attach(spy, workflow_id="wf-C")
    bus_before = get_log_bus("wf-C")
    task_before = bus_before._drain_task
    assert task_before is not None and not task_before.done()
    monkeypatch.setattr(
        "supernova_core.logging.log_bus._resolve_wf_id",
        lambda explicit=None: "wf-C",
    )

    await LogBus.drain_and_detach()  # proxy 路径（finalize_summary 同款调用）

    assert task_before.done(), "旧 bus 的 drain task 应被 cancel + await 收尾"
    bus_after = get_log_bus("wf-C")
    assert bus_after is not bus_before, "收尾应 pop 旧 bus（后续 get 重建占位）"
    assert not bus_after.is_attached
    assert bus_after._dispatcher is None
    assert bus_after._drain_task is None
    # diagnostic 已是进程级单例（不在 bus 上）：占位 bus 天然不携带。


@pytest.mark.asyncio
async def test_handler_emit_routes_to_resolved_workflow_bus(monkeypatch):
    """root 共享单 handler：emit 按 _resolve_wf_id 动态路由——多 wf 并发不串台的根本。

    （替代 plan 的「构造时绑定 workflow_id」：root 单槽会被并发 setup_display 互相替换，
    绑死 wf 反而串台；emit 动态路由才正确。）
    """
    spy = _spy()
    await attach(spy, workflow_id="wf-X")
    monkeypatch.setattr(
        "supernova_core.logging.log_bus._resolve_wf_id",
        lambda explicit=None: "wf-X",
    )
    h = LogBusHandler()  # 单 handler，不绑死 wf
    rec = logging.LogRecord(
        "x", logging.WARNING, "", 0, "route-msg", None, None
    )
    h.emit(rec)
    await asyncio.sleep(0.15)
    await drain_and_detach(workflow_id="wf-X")
    spy.dispatch.assert_awaited()  # event 经 wf-X bus drain 到 dispatcher
