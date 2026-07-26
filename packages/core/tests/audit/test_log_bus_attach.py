"""统一日志总线 Task4: attach/detach + drain task + run_with_display 接线。

spec/plan: docs/superpowers/specs/2026-07-08-unified-logging-facade-design.md
        / docs/superpowers/plans/2026-07-08-unified-log-bus.md（task 4）

session 活跃时（run_with_display 内），LogBus.attach(session.dispatcher) 起 drain
task，散落 logging 经 dispatcher 上屏（与 PHASE/STEP 同 asyncio.Lock 序列化）；退出时
drain_and_detach final flush + cancel drain。session.dispatcher property 暴露
workflow_logger._dispatcher 供 attach 拿引用；attach 时自动挂 DiagnosticLogRenderer
让 LogEvent 经 dispatcher 落盘 diagnostic.log。
"""
from __future__ import annotations

import asyncio
import logging
import queue as _queue
from io import StringIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from rich.console import Console

from supernova_core.display.dispatcher import DisplayDispatcher
from supernova_core.display.events import LogEvent
from supernova_core.display.rich_renderer import RichConsoleRenderer
from supernova_core.logging import LogBus
from supernova_core.logging.log_bus import _BUSES
from supernova_core.logging.setup import configure_logging


@pytest.fixture(autouse=True)
def _restore_root_logger():
    root = logging.getLogger()
    saved = list(root.handlers)
    saved_level = root.level
    try:
        yield
    finally:
        for h in list(root.handlers):
            if h not in saved:
                root.removeHandler(h)
                h.close()
        for h in saved:
            if h not in root.handlers:
                root.addHandler(h)
        root.setLevel(saved_level)


@pytest.fixture(autouse=True)
async def _restore_log_bus():
    # P3c 阶段 3：LogBus 是 _BUSES dict 的代理（__getattr__ 转发到当前 workflow 的 bus）。
    # 旧版写 LogBus._xxx 会落在代理 instance __dict__、不触达真实 bus，且后续读命中
    # instance dict 不走 __getattr__ → 跨 test 污染。改为清 _BUSES 注册表。
    yield
    for bus in list(_BUSES.values()):
        bus._attached = False
        bus._dispatcher = None
        if bus._drain_task is not None and not bus._drain_task.done():
            bus._drain_task.cancel()
        bus._drain_task = None
        if bus._diagnostic is not None:
            bus._diagnostic.close()
            bus._diagnostic = None
        while True:
            try:
                bus.queue.get_nowait()
            except _queue.Empty:
                break
    _BUSES.clear()


# --- attach/detach 状态机 + drain 链路 ---

async def test_attach_sets_attached_and_detach_clears(tmp_path):
    configure_logging(log_dir=tmp_path)
    spy = MagicMock()
    spy.dispatch = AsyncMock()  # add() is sync on real DisplayDispatcher; only dispatch is async
    assert not LogBus.is_attached
    await LogBus.attach(spy)
    assert LogBus.is_attached
    await LogBus.drain_and_detach()
    assert not LogBus.is_attached
    assert LogBus._dispatcher is None


async def test_logging_routes_through_dispatcher_to_console(tmp_path):
    """attach 后散落 logging 经 drain → dispatcher → RichConsoleRenderer 上屏。"""
    configure_logging(log_dir=tmp_path)
    buf = StringIO()
    console = Console(file=buf, width=200, color_system=None)
    dispatcher = DisplayDispatcher([RichConsoleRenderer(console)])
    await LogBus.attach(dispatcher)
    try:
        logging.getLogger("supernova_core.code_index").warning("routed-msg")
        await asyncio.sleep(0.2)  # 等 drain（sleep 0.05 一轮）
    finally:
        await LogBus.drain_and_detach()
    out = buf.getvalue()
    assert "routed-msg" in out
    assert "code_index" in out


async def test_drain_task_drains_remaining_on_detach(tmp_path):
    """drain_and_detach final flush：detach 前排空 queue 剩余。"""
    configure_logging(log_dir=tmp_path)
    spy = MagicMock()
    spy.dispatch = AsyncMock()  # add() is sync on real DisplayDispatcher; only dispatch is async
    await LogBus.attach(spy)
    for i in range(3):
        LogBus.queue.put_nowait(LogEvent(timestamp="t", category="WARNING",
            logger_name="x", level="WARNING", message=f"m{i}", exc_txt=None))
    await asyncio.sleep(0.2)
    await LogBus.drain_and_detach()
    assert LogBus.queue.qsize() == 0
    assert spy.dispatch.await_count >= 3


async def test_lastresort_never_engages_when_attached(tmp_path, capsys):
    """attach 模式下 LogBusHandler emit 走 queue，不触发 lastResort（stderr）。"""
    configure_logging(log_dir=tmp_path)
    spy = MagicMock()
    spy.dispatch = AsyncMock()  # add() is sync on real DisplayDispatcher; only dispatch is async
    await LogBus.attach(spy)
    try:
        logging.getLogger("supernova_core.y").error("attached-boom")
        await asyncio.sleep(0.2)
    finally:
        await LogBus.drain_and_detach()
    captured = capsys.readouterr()
    assert "attached-boom" not in captured.err


# --- run_with_display 接线（session.dispatcher property + attach/detach）---

async def test_run_with_display_attaches_log_bus(tmp_path):
    """run_with_display 在 session.initialize 后 attach LogBus 到 session.dispatcher。"""
    from supernova_core.audit.display_lifecycle import run_with_display
    from supernova_core.models.metrics import SessionMetadata
    configure_logging(log_dir=tmp_path)
    meta = SessionMetadata(id="wf-attach", web_url="", repo_path=str(tmp_path),
                           output_path=str(tmp_path))
    async with run_with_display(meta, use_rich=False) as session:
        assert LogBus.is_attached
        assert LogBus._dispatcher is session.dispatcher
    assert not LogBus.is_attached  # 退出后 detach


async def test_logging_routes_via_dispatcher_inside_run_with_display(tmp_path, capsys):
    """run_with_display 内散落 logging 经 dispatcher 上屏、落 diagnostic.log、不落 stderr。"""
    from supernova_core.audit.display_lifecycle import run_with_display
    from supernova_core.models.metrics import SessionMetadata
    configure_logging(log_dir=tmp_path)
    meta = SessionMetadata(id="wf-route", web_url="", repo_path=str(tmp_path),
                           output_path=str(tmp_path))
    async with run_with_display(meta, use_rich=False):
        logging.getLogger("supernova_core.inside").warning("inside-msg")
        await asyncio.sleep(0.2)
    captured = capsys.readouterr()
    assert "inside-msg" not in captured.err
    assert "inside-msg" in (tmp_path / "diagnostic.log").read_text()


async def test_logging_not_mixed_into_workflow_log(tmp_path):
    """clean 分离：诊断 logging 不进 workflow.log（FileLogRenderer 对 LogEvent no-op）。"""
    from supernova_core.audit.display_lifecycle import run_with_display
    from supernova_core.models.metrics import SessionMetadata
    configure_logging(log_dir=tmp_path)
    meta = SessionMetadata(id="wf-clean", web_url="", repo_path=str(tmp_path),
                           output_path=str(tmp_path))
    async with run_with_display(meta, use_rich=False):
        logging.getLogger("supernova_core.diag").warning("diag-only-msg")
        await asyncio.sleep(0.2)
    # workflow.log 由 generate_workflow_log_path 决定；找它
    wf_logs = list(tmp_path.rglob("workflow.log"))
    assert wf_logs, "应生成 workflow.log"
    wf_text = wf_logs[0].read_text()
    assert "diag-only-msg" not in wf_text
