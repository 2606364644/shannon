"""TDD for supernova_core.logging.setup.configure_logging（统一日志总线版）。

spec: docs/superpowers/specs/2026-07-08-unified-logging-facade-design.md
plan: docs/superpowers/plans/2026-07-08-unified-log-bus.md
configure_logging 挂 LogBusHandler（替代 stderr+FileHandler），散落 getLogger 自动
汇入日志总线（无 session→diagnostic.log fallback；session 活跃→event-loop drain）。
"""
from __future__ import annotations

import logging
import os
import queue as _queue
from pathlib import Path

import pytest

from supernova_core.logging import LogBus, LogBusHandler
from supernova_core.logging.diagnostic_log import format_diagnostic_line
from supernova_core.logging.setup import configure_logging

_TEMPORALIO_LOGGER = "temporalio.activity"


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """root logger 是进程级单例，configure_logging 改它会泄漏到其他测试。

    snapshot handlers/level，teardown 还原（close 新增 handler 释放 fd）。
    """
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
def _restore_temporalio_logger():
    """temporalio.activity logger 同为全局单例，snapshot/restore 防 leak。"""
    logger = logging.getLogger(_TEMPORALIO_LOGGER)
    saved_handlers = list(logger.handlers)
    saved_propagate = logger.propagate
    saved_level = logger.level
    try:
        yield
    finally:
        for h in list(logger.handlers):
            if h not in saved_handlers:
                logger.removeHandler(h)
                h.close()
        for h in saved_handlers:
            if h not in logger.handlers:
                logger.addHandler(h)
        logger.propagate = saved_propagate
        logger.setLevel(saved_level)


@pytest.fixture(autouse=True)
def _restore_log_bus():
    """LogBus 是模块级单例，configure_logging/attach 改其状态会泄漏到其他测试：
    teardown 重置 _dispatcher/_attached/_drain_task、关 diagnostic、清 queue。"""
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


def _our_handlers(root_or_logger=None) -> list[logging.Handler]:
    """只数 configure_logging 加的 handler（带 _shannon_configured 标记）。

    root 上可能存在调用方/其他机制预加的 handler（如 pytest 预置 /dev/null），
    那些不该计入 configure_logging 的断言——我们只保证自己不堆叠。
    """
    logger = root_or_logger or logging.getLogger()
    return [h for h in logger.handlers if getattr(h, "_shannon_configured", False)]


# --- RED 1: log_dir 不存在时自动创建 ---

def test_log_dir_created_if_missing(tmp_path):
    log_dir = tmp_path / "nested" / "logs"
    assert not log_dir.exists()
    configure_logging(log_dir=log_dir)
    assert log_dir.exists()
    assert log_dir.is_dir()


def test_diagnostic_log_file_created(tmp_path):
    log_dir = tmp_path / "logs"
    configure_logging(log_dir=log_dir)
    assert (log_dir / "diagnostic.log").exists()


# --- RED 2: root logger 挂单个 LogBusHandler（不再 stderr+FileHandler）---

def test_root_has_logbus_handler_only(tmp_path):
    """configure_logging 只挂单个 LogBusHandler（替代 stderr+FileHandler）。"""
    configure_logging(log_dir=tmp_path)
    bus_handlers = [h for h in _our_handlers() if isinstance(h, LogBusHandler)]
    assert len(bus_handlers) == 1, "应挂单个 LogBusHandler"


def test_format_diagnostic_line_padded_level():
    """format_diagnostic_line level 右对齐 5 列（对齐 _FORMAT [%(levelname)5s]）。"""
    from supernova_core.display.events import LogEvent
    line = format_diagnostic_line(LogEvent(
        timestamp="t", category="INFO", logger_name="x", level="INFO",
        message="hi", exc_txt=None))
    assert "[ INFO]" in line  # 右对齐 5


# --- RED 3: 幂等性（同 log_dir 重复调用不重复 handler）---

def test_idempotent_same_log_dir(tmp_path):
    configure_logging(log_dir=tmp_path)
    configure_logging(log_dir=tmp_path)
    bus_handlers = [h for h in _our_handlers() if isinstance(h, LogBusHandler)]
    assert len(bus_handlers) == 1, "同 log_dir 重复调用不应重复挂 LogBusHandler"


def test_idempotent_replaces_when_log_dir_changes(tmp_path):
    """不同 log_dir：LogBusHandler 不堆叠 + diagnostic.log 指向新目录。"""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    configure_logging(log_dir=dir_a)
    configure_logging(log_dir=dir_b)
    bus_handlers = [h for h in _our_handlers() if isinstance(h, LogBusHandler)]
    assert len(bus_handlers) == 1, "log_dir 变更不堆叠 LogBusHandler"
    assert LogBus._diagnostic.path == (dir_b / "diagnostic.log").resolve()


# --- RED 4: temporalio.activity logger 不被收编（propagate=False 保持、handlers 不被加）---

def test_temporalio_activity_not_captured(tmp_path):
    """configure_logging 不对 temporalio.activity 加 handler、不改 propagate=False。"""
    configure_logging(log_dir=tmp_path)
    logger = logging.getLogger(_TEMPORALIO_LOGGER)
    new_file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)
                         and Path(h.baseFilename).resolve() == (tmp_path / "diagnostic.log").resolve()]
    assert new_file_handlers == [], "configure_logging 不应把 temporalio.activity 收编进 diagnostic.log"


def test_temporalio_propagate_stays_false_after_redirect(tmp_path):
    """若 temporalio_redirect 已设 propagate=False，configure_logging 不应翻回 True。"""
    from supernova_core.logging.temporalio_redirect import install_temporalio_log_redirect
    install_temporalio_log_redirect(tmp_path / "activity.log")
    logger = logging.getLogger(_TEMPORALIO_LOGGER)
    assert logger.propagate is False
    configure_logging(log_dir=tmp_path)
    assert logger.propagate is False, "configure_logging 不应改 temporalio propagate"


# --- RED 5: 散落 getLogger 经 LogBusHandler 写 diagnostic.log + 不落 stderr ---

def test_third_party_logger_uses_root_formatter(tmp_path, capsys):
    """散落 getLogger 的 WARNING 经 LogBusHandler fallback 写 diagnostic.log（零改动统一），
    且不落 stderr（防鬼影根）。--验证 ~20 处调用点零改动即统一汇入。"""
    configure_logging(log_dir=tmp_path)
    logging.getLogger("supernova_core.services.some_service").warning("queue unreadable: boom")
    log_text = (tmp_path / "diagnostic.log").read_text()
    assert "queue unreadable: boom" in log_text
    assert "some_service" in log_text
    assert "WARNING" in log_text
    captured = capsys.readouterr()
    assert "queue unreadable" not in captured.err  # 防鬼影根：不落 stderr


# --- RED 6: SUPERNOVA_LOG_LEVEL 覆盖 root level ---

def test_shannong_log_level_override(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERNOVA_LOG_LEVEL", "DEBUG")
    configure_logging(log_dir=tmp_path)
    assert logging.getLogger().level == logging.DEBUG


def test_third_party_noise_libraries_capped_at_warning(tmp_path):
    """httpx/urllib3/httpcore/asyncio 默认 WARNING，不随 root level 放开刷屏。"""
    configure_logging(log_dir=tmp_path)
    for noisy in ("httpx", "urllib3", "httpcore", "asyncio"):
        assert logging.getLogger(noisy).level == logging.WARNING, f"{noisy} 应被限到 WARNING"


# --- RED 7: claude_agent_sdk 噪声 + temporalio Rust core 降级（spec 2026-07-14 §模块1）---

def test_claude_agent_sdk_capped_at_warning(tmp_path):
    """claude_agent_sdk 的 INFO(Using bundled Claude Code CLI) 限到 WARNING，不刷屏。"""
    configure_logging(log_dir=tmp_path)
    assert logging.getLogger("claude_agent_sdk").level == logging.WARNING


def test_rust_log_setdefault_when_unset(tmp_path, monkeypatch):
    """RUST_LOG 未设时 configure_logging 默认压 temporalio Rust core 到 error。"""
    monkeypatch.delenv("RUST_LOG", raising=False)
    configure_logging(log_dir=tmp_path)
    assert os.environ.get("RUST_LOG") == "temporalio_sdk_core=error"


def test_rust_log_not_overwritten_when_user_set(tmp_path, monkeypatch):
    """用户已设 RUST_LOG 时 configure_logging 不覆盖（setdefault 语义）。"""
    monkeypatch.setenv("RUST_LOG", "temporalio_sdk_core=debug")
    configure_logging(log_dir=tmp_path)
    assert os.environ["RUST_LOG"] == "temporalio_sdk_core=debug"
