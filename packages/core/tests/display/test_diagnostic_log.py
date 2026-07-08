"""统一日志总线 Task2: DiagnosticLog + DiagnosticLogRenderer。

spec/plan: 2026-07-08-unified-logging-facade / 2026-07-08-unified-log-bus（task 2）

DiagnosticLog：单一 sync 文件句柄 + threading.Lock，被生产线程 fallback 和
event-loop DiagnosticLogRenderer 共用。行频低、单行 <1ms，不用 aiofiles。
DiagnosticLogRenderer：match LogEvent → diag.write_sync([ts] [LEVEL5] name: msg + exc_text)，
经 dispatcher.add 运行时挂（不进 workflow.log，clean 分离）。

格式对齐 setup._FORMAT 的 [%(levelname)5s]（右对齐 5），与原 logging diagnostic.log 一致。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from shannon_core.display.events import InfoEvent, LogEvent
from shannon_core.display.file_renderer import DiagnosticLogRenderer
from shannon_core.logging.diagnostic_log import DiagnosticLog


# --- DiagnosticLog 单元（sync 文件句柄 + Lock）---

def test_diagnostic_log_write_sync_appends_line(tmp_path):
    diag = DiagnosticLog(tmp_path / "diagnostic.log")
    diag.write_sync("[t] [ INFO] x: boom\n")
    diag.close()
    assert "[t] [ INFO] x: boom\n" in (tmp_path / "diagnostic.log").read_text()


def test_diagnostic_log_write_sync_multiple_lines(tmp_path):
    diag = DiagnosticLog(tmp_path / "diagnostic.log")
    diag.write_sync("a\n")
    diag.write_sync("b\n")
    diag.close()
    assert (tmp_path / "diagnostic.log").read_text() == "a\nb\n"


def test_diagnostic_log_close_is_idempotent(tmp_path):
    diag = DiagnosticLog(tmp_path / "diagnostic.log")
    diag.write_sync("x\n")
    diag.close()
    diag.close()  # 二次 close 不崩


def test_diagnostic_log_parent_dir_created(tmp_path):
    target = tmp_path / "nested" / "logs" / "diagnostic.log"
    diag = DiagnosticLog(target)
    diag.write_sync("x\n")
    diag.close()
    assert target.exists()


# --- DiagnosticLogRenderer（match LogEvent → write_sync）---

async def test_diagnostic_log_renderer_writes_line(tmp_path):
    diag = DiagnosticLog(tmp_path / "diagnostic.log")
    r = DiagnosticLogRenderer(diag)
    evt = LogEvent(timestamp="2026-07-08 10:00:00", category="WARNING",
                   logger_name="shannon_core.code_index", level="WARNING",
                   message="queue unreadable: boom", exc_txt=None)
    await r.render(evt)
    diag.close()
    text = (tmp_path / "diagnostic.log").read_text()
    assert ("[2026-07-08 10:00:00] [WARNING] shannon_core.code_index: "
            "queue unreadable: boom\n") in text


async def test_diagnostic_log_renderer_level_right_aligned(tmp_path):
    """level 右对齐 5 列（对齐 setup._FORMAT [%(levelname)5s]）：INFO → ' INFO'。"""
    diag = DiagnosticLog(tmp_path / "diagnostic.log")
    r = DiagnosticLogRenderer(diag)
    await r.render(LogEvent(timestamp="t", category="INFO", logger_name="x",
                            level="INFO", message="hi", exc_txt=None))
    diag.close()
    assert "[ INFO]" in (tmp_path / "diagnostic.log").read_text()


async def test_diagnostic_log_renderer_appends_exc_text(tmp_path):
    diag = DiagnosticLog(tmp_path / "diagnostic.log")
    r = DiagnosticLogRenderer(diag)
    await r.render(LogEvent(timestamp="t", category="ERROR", logger_name="x",
                            level="ERROR", message="boom",
                            exc_txt="Traceback (most recent call last):\n  File x"))
    diag.close()
    assert "Traceback" in (tmp_path / "diagnostic.log").read_text()


async def test_diagnostic_log_renderer_ignores_non_logevent(tmp_path):
    """DiagnosticLogRenderer 只 match LogEvent，其他 event 落空不写。"""
    diag = DiagnosticLog(tmp_path / "diagnostic.log")
    r = DiagnosticLogRenderer(diag)
    await r.render(InfoEvent(timestamp="t", category="INFO", message="hi"))
    diag.close()
    assert (tmp_path / "diagnostic.log").read_text() == ""
