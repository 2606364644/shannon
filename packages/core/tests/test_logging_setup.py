"""TDD for shannon_core.logging.setup.configure_logging.

spec: docs/superpowers/specs/2026-07-08-unified-logging-facade-design.md
组件 1：dictConfig 统一入口（等宽 LEVEL 列、幂等、log_dir 自建、跳过 temporalio.activity）。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from shannon_core.logging.setup import configure_logging

_TEMPORALIO_LOGGER = "temporalio.activity"


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """root logger 是进程级单例，configure_logging 改它会泄漏到其他测试。

    snapshot handlers/level/formatter，teardown 还原（close 新增 FileHandler 释放 fd）。
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


def _our_handlers(root_or_logger=None) -> list[logging.Handler]:
    """只数 configure_logging 加的 handler（带 _shannon_configured 标记）。

    root 上可能存在调用方/其他机制预加的 handler（如 pytest 预置 /dev/null），
    那些不该计入 configure_logging 的幂等性断言--我们只保证自己不堆叠。
    """
    logger = root_or_logger or logging.getLogger()
    return [h for h in logger.handlers if getattr(h, "_shannon_configured", False)]


def _file_handlers(root_or_logger=None) -> list[logging.FileHandler]:
    return [h for h in _our_handlers(root_or_logger) if isinstance(h, logging.FileHandler)]


def _stderr_handlers(root=None) -> list[logging.StreamHandler]:
    return [h for h in _our_handlers(root)
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
            and getattr(h, "stream", None) is sys.stderr]


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


# --- RED 2: root logger 套上 stderr + FileHandler，共用 Formatter 含等宽 LEVEL 列 ---


def test_root_has_stderr_and_file_handler(tmp_path):
    configure_logging(log_dir=tmp_path)
    root = logging.getLogger()
    assert _stderr_handlers(root), "缺 stderr StreamHandler"
    assert _file_handlers(root), "缺 diagnostic.log FileHandler"


def test_formatter_has_padded_level_column(tmp_path):
    """FORMATTER 含 [%(levelname)5s] 等宽 LEVEL 列（对齐 display tag/LABEL_WIDTH=5）。"""
    configure_logging(log_dir=tmp_path)
    our = _our_handlers(logging.getLogger())
    assert our, "configure_logging 未加任何 handler"
    formatter = our[0].formatter
    fmt = formatter._fmt if formatter else ""
    assert "%(levelname)" in fmt
    # 等宽：%(levelname)5s 或 %(levelname)-5s 任一形式；INFO/WARN/ERROR 输出占 5 列对齐
    assert "5" in fmt, f"FORMATTER 缺等宽 LEVEL 列: {fmt}"


# --- RED 3: 幂等性（同 log_dir 重复调用不重复 handler）---


def test_idempotent_same_log_dir(tmp_path):
    configure_logging(log_dir=tmp_path)
    configure_logging(log_dir=tmp_path)
    root = logging.getLogger()
    assert len(_file_handlers(root)) == 1, "同 log_dir 重复调用不应重复加 FileHandler"
    assert len(_stderr_handlers(root)) == 1, "同 log_dir 重复调用不应重复加 stderr handler"


def test_idempotent_replaces_when_log_dir_changes(tmp_path):
    """不同 log_dir：替换 FileHandler（指向新文件），不堆叠。"""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    configure_logging(log_dir=dir_a)
    configure_logging(log_dir=dir_b)
    fh_list = _file_handlers(logging.getLogger())
    assert len(fh_list) == 1, "log_dir 变更应替换而非堆叠 FileHandler"
    assert Path(fh_list[0].baseFilename).resolve() == (dir_b / "diagnostic.log").resolve()


# --- RED 4: temporalio.activity logger 不被收编（propagate=False 保持、handlers 不被加）---


def test_temporalio_activity_not_captured(tmp_path):
    """configure_logging 不对 temporalio.activity 加 handler、不改 propagate=False。"""
    configure_logging(log_dir=tmp_path)
    logger = logging.getLogger(_TEMPORALIO_LOGGER)
    # configure_logging 不应给 temporalio.activity 加任何 handler（由 temporalio_redirect 独立管）
    new_file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)
                         and Path(h.baseFilename).resolve() == (tmp_path / "diagnostic.log").resolve()]
    assert new_file_handlers == [], "configure_logging 不应把 temporalio.activity 收编进 diagnostic.log"


def test_temporalio_propagate_stays_false_after_redirect(tmp_path):
    """若 temporalio_redirect 已设 propagate=False，configure_logging 不应翻回 True。"""
    from shannon_core.logging.temporalio_redirect import install_temporalio_log_redirect
    install_temporalio_log_redirect(tmp_path / "activity.log")
    logger = logging.getLogger(_TEMPORALIO_LOGGER)
    assert logger.propagate is False
    configure_logging(log_dir=tmp_path)
    assert logger.propagate is False, "configure_logging 不应改 temporalio propagate"


# --- RED 5: 散落 getLogger 自动套 root 格式（A 档核心收益验证）---


def test_third_party_logger_uses_root_formatter(tmp_path, capsys):
    """散落 logging.getLogger(__name__) 的 WARNING 经 propagate 走 root handler，
    套上等宽 LEVEL 格式、写入 diagnostic.log。--验证 ~20 处调用点零改动即统一格式。"""
    configure_logging(log_dir=tmp_path)
    # 模拟某 service 的散落 logger
    logging.getLogger("shannon_core.services.some_service").warning("queue unreadable: boom")
    for h in logging.getLogger().handlers:
        h.flush()
    log_text = (tmp_path / "diagnostic.log").read_text()
    assert "queue unreadable: boom" in log_text
    assert "some_service" in log_text
    assert "WARNING" in log_text


# --- RED 6: SHANNON_LOG_LEVEL 覆盖 root level ---


def test_shannong_log_level_override(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANNON_LOG_LEVEL", "DEBUG")
    configure_logging(log_dir=tmp_path)
    assert logging.getLogger().level == logging.DEBUG


def test_third_party_noise_libraries_capped_at_warning(tmp_path):
    """httpx/urllib3/httpcore/asyncio 默认 WARNING，不随 root level 放开刷屏。"""
    configure_logging(log_dir=tmp_path)
    for noisy in ("httpx", "urllib3", "httpcore", "asyncio"):
        assert logging.getLogger(noisy).level == logging.WARNING, f"{noisy} 应被限到 WARNING"
