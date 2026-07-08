"""统一日志总线 Task3: LogBus + LogBusHandler + configure_logging 改挂。

spec/plan: docs/superpowers/specs/2026-07-08-unified-logging-facade-design.md
        / docs/superpowers/plans/2026-07-08-unified-log-bus.md（task 3）

LogBusHandler 挂在 root（替代 StreamHandler(stderr)+FileHandler）：
- 生产线程 emit 只 prepare（物化 message）+ 分流（is_attached?queue:fallback），
  不碰 console/stderr/rich/import（sandbox 安全）；
- 无 session → DiagnosticLog.write_sync（threading.Lock）；
- session 活跃 → queue.put_nowait(LogEvent) → event-loop drain（task 4 接）。

防鬼影根：logging 不再直写 stderr（test_logging_never_writes_stderr）。
"""
from __future__ import annotations

import logging
import queue as _queue
import sys
from datetime import datetime

import pytest

from shannon_core.logging import LogBus, LogBusHandler
from shannon_core.logging.setup import configure_logging


# --- fixtures：restore root logger + LogBus 单例（防跨测试泄漏）---

@pytest.fixture(autouse=True)
def _restore_root_logger():
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        yield
    finally:
        for h in list(root.handlers):
            if h not in saved_handlers:
                root.removeHandler(h)
                h.close()
        for h in saved_handlers:
            if h not in root.handlers:
                root.addHandler(h)
        root.setLevel(saved_level)


@pytest.fixture(autouse=True)
def _restore_log_bus():
    try:
        yield
    finally:
        LogBus._dispatcher = None
        LogBus._attached = False
        LogBus._drain_task = None
        if LogBus._diagnostic is not None:
            LogBus._diagnostic.close()
            LogBus._diagnostic = None
        while True:
            try:
                LogBus.queue.get_nowait()
            except _queue.Empty:
                break


# --- configure_logging 改挂 LogBusHandler ---

def test_configure_logging_attaches_single_logbus_handler(tmp_path):
    configure_logging(log_dir=tmp_path)
    bus_handlers = [h for h in logging.getLogger().handlers if isinstance(h, LogBusHandler)]
    assert len(bus_handlers) == 1, "configure_logging 应挂单个 LogBusHandler"


def test_configure_logging_no_stderr_handler(tmp_path):
    """防鬼影根：configure_logging 加的 handler 中无 StreamHandler(stderr)。

    只查 _shannon_configured handler（pytest 预置的 /dev/null handler 不计入）。"""
    configure_logging(log_dir=tmp_path)
    our = [h for h in logging.getLogger().handlers if getattr(h, "_shannon_configured", False)]
    stderr_handlers = [
        h for h in our
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        and getattr(h, "stream", None) is sys.stderr
    ]
    assert stderr_handlers == [], "不应再有 StreamHandler(stderr)"


def test_configure_logging_no_file_handler(tmp_path):
    """diagnostic.log 由 DiagnosticLog 管（非 logging.FileHandler）。

    只查 _shannon_configured handler（pytest 预置的 /dev/null handler 不计入）。"""
    configure_logging(log_dir=tmp_path)
    our = [h for h in logging.getLogger().handlers if getattr(h, "_shannon_configured", False)]
    file_handlers = [h for h in our if isinstance(h, logging.FileHandler)]
    assert file_handlers == [], "不应再有 logging.FileHandler"


def test_configure_logging_idempotent_bus_handler(tmp_path):
    """同 log_dir 重复调用不重复挂 LogBusHandler。"""
    configure_logging(log_dir=tmp_path)
    configure_logging(log_dir=tmp_path)
    bus_handlers = [h for h in logging.getLogger().handlers if isinstance(h, LogBusHandler)]
    assert len(bus_handlers) == 1


def test_configure_logging_configures_diagnostic(tmp_path):
    """configure_logging 调 log_bus.configure_diagnostic(diagnostic.log)。"""
    configure_logging(log_dir=tmp_path)
    assert LogBus._diagnostic is not None
    assert LogBus._diagnostic.path == (tmp_path / "diagnostic.log").resolve()


def test_configure_logging_creates_diagnostic_log_file(tmp_path):
    configure_logging(log_dir=tmp_path)
    # fallback 路径打开文件即创建
    assert (tmp_path / "diagnostic.log").exists()


# --- 防鬼影根：logging 不直写 stderr ---

def test_logging_never_writes_stderr(tmp_path, capsys):
    """散落 logger.warning 经 LogBusHandler，不落 stderr（防鬼影根）。"""
    configure_logging(log_dir=tmp_path)
    logging.getLogger("shannon_core.services.x").warning("queue unreadable: boom")
    captured = capsys.readouterr()
    assert "queue unreadable: boom" not in captured.err


def test_lastresort_never_engages(tmp_path, capsys):
    """LogBusHandler emit 不抛 → record handled → lastResort（硬编码 stderr）不触发。"""
    configure_logging(log_dir=tmp_path)
    logging.getLogger("shannon_core.services.y").error("real boom")
    captured = capsys.readouterr()
    assert "real boom" not in captured.err


# --- 无 session fallback → diagnostic.log ---

def test_logging_falls_back_to_diagnostic_log_without_session(tmp_path):
    """无 session（is_attached=False）→ LogBusHandler emit 走 diagnostic.log fallback。"""
    configure_logging(log_dir=tmp_path)
    assert not LogBus.is_attached
    logging.getLogger("shannon_core.code_index").warning("queue unreadable: boom")
    text = (tmp_path / "diagnostic.log").read_text()
    assert "queue unreadable: boom" in text
    assert "WARNING" in text
    assert "code_index" in text


def test_fallback_records_exc_info_into_diagnostic(tmp_path):
    """带 exc_info 的 record fallback 时把 traceback 写进 diagnostic.log。"""
    configure_logging(log_dir=tmp_path)
    log = logging.getLogger("shannon_core.z")
    try:
        raise RuntimeError("kaboom")
    except RuntimeError:
        log.error("failed", exc_info=True)
    text = (tmp_path / "diagnostic.log").read_text()
    assert "Traceback" in text
    assert "RuntimeError" in text


# --- sandbox 安全：emit 不碰 console/stderr，只 prepare+分流 ---

def test_logbus_handler_emit_does_not_touch_console_or_stderr(tmp_path, capsys):
    """LogBusHandler.emit 在生产线程只 prepare+fallback，不碰 console/stderr。"""
    configure_logging(log_dir=tmp_path)
    handler = next(h for h in logging.getLogger().handlers if isinstance(h, LogBusHandler))
    record = logging.LogRecord(
        name="shannon_core.x", level=logging.WARNING, pathname=__file__, lineno=1,
        msg="boom-in-sandbox", args=None, exc_info=None)
    handler.emit(record)
    captured = capsys.readouterr()
    assert "boom-in-sandbox" not in captured.err   # emit 不写 stderr
    assert "boom-in-sandbox" in (tmp_path / "diagnostic.log").read_text()  # 走 fallback


def test_logbus_handler_emit_queues_when_attached(tmp_path):
    """is_attached=True → emit 走 queue.put_nowait（不碰 console，drain 在 task 4）。"""
    configure_logging(log_dir=tmp_path)
    LogBus._attached = True  # 模拟 session 已 attach（drain 由 task 4 接，这里只验分流）
    logging.getLogger("shannon_core.q").warning("queued-msg")
    # queue 里应有 LogEvent，diagnostic.log 不应有（走了 queue 而非 fallback）
    drained = []
    while True:
        try:
            drained.append(LogBus.queue.get_nowait())
        except _queue.Empty:
            break
    assert any(getattr(e, "message", None) == "queued-msg" for e in drained)
    assert "queued-msg" not in (tmp_path / "diagnostic.log").read_text()


def test_logbus_handler_prepare_builds_logevent(tmp_path):
    """prepare 物化 message + 构造 LogEvent（带 logger_name/level/exc_txt）。"""
    from shannon_core.display.events import LogEvent
    configure_logging(log_dir=tmp_path)
    handler = next(h for h in logging.getLogger().handlers if isinstance(h, LogBusHandler))
    record = logging.LogRecord(
        name="shannon_core.foo", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="err %s", args=("x",), exc_info=None)
    event = handler.prepare(record)
    assert isinstance(event, LogEvent)
    assert event.message == "err x"            # getMessage 物化 %s
    assert event.level == "ERROR"
    assert event.logger_name == "shannon_core.foo"
    assert event.category == "ERROR"
