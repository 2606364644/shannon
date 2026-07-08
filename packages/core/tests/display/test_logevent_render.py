"""统一日志总线 Task1: LogEvent + rich/file 渲染。

spec: docs/superpowers/specs/2026-07-08-unified-logging-facade-design.md §4
plan: docs/superpowers/plans/2026-07-08-unified-log-bus.md（task 1）

LogEvent 是诊断 logging 流的 DisplayEvent 载体（对偶 InfoEvent 的 workflow 用户消息）：
- rich 渲染按 level 着色、无扫描符号（spec §4 铁律：诊断不套 ▶/✓/✗/○，用等宽 LEVEL 列）；
- file renderer 对 LogEvent 落空（诊断不进 workflow.log，clean 分离 → 独立 diagnostic.log）。
"""
from io import StringIO
from unittest.mock import AsyncMock

import pytest
from rich.console import Console

from shannon_core.display.events import LogEvent
from shannon_core.display.file_renderer import FileLogRenderer
from shannon_core.display.rich_renderer import RichConsoleRenderer


def _evt(level="WARNING", **kw):
    base = dict(
        timestamp="2026-07-08 10:00:00",
        category=level,
        logger_name="shannon_core.code_index",
        level=level,
        message="queue unreadable: boom",
        exc_txt=None,
    )
    base.update(kw)
    return LogEvent(**base)


# --- 无扫描符号（spec §4 铁律）---

@pytest.mark.parametrize("symbol", ["▶", "✓", "✗", "○"])
def test_logevent_render_no_scan_symbols(symbol):
    """诊断 log 行不得含扫描符号 ▶/✓/✗/○（这些是 STEP/AGENT 状态符号，与诊断语义无关）。"""
    buf = StringIO()
    r = RichConsoleRenderer(Console(file=buf, width=200, color_system=None))
    r._render_log(_evt(level="WARNING"))
    out = buf.getvalue()
    assert symbol not in out, f"诊断 log 行不得含扫描符号 {symbol}: {out!r}"


def test_logevent_render_includes_logger_level_message():
    buf = StringIO()
    r = RichConsoleRenderer(Console(file=buf, width=200, color_system=None))
    r._render_log(_evt(level="WARNING"))
    out = buf.getvalue()
    assert "shannon_core.code_index" in out
    assert "WARNING" in out
    assert "queue unreadable: boom" in out


# --- 各 level 着色（markup 断言）---

@pytest.mark.parametrize("level,color", [
    ("ERROR", "bold red"),
    ("CRITICAL", "bold red"),
    ("WARNING", "yellow"),
    ("INFO", "cyan"),
    ("DEBUG", "dim"),
])
def test_logevent_level_color_markup(level, color):
    """每个 level 选对颜色 markup：ERROR/CRITICAL=bold red、WARNING=yellow、INFO=cyan、DEBUG=dim。"""
    buf = StringIO()
    console = Console(file=buf, width=200, color_system=None)
    r = RichConsoleRenderer(console)
    captured = []
    orig = console.print

    def spy(*a, **k):
        if a:
            captured.append(str(a[0]))
        return orig(*a, **k)

    console.print = spy
    r._render_log(_evt(level=level))
    assert any(f"[{color}]" in c for c in captured), (
        f"level={level} 应使用 markup [{color}]，实际 printed={captured}")


def test_logevent_exc_text_appended():
    """exc_txt 非空时附在诊断行后（异常追踪可见）。"""
    buf = StringIO()
    r = RichConsoleRenderer(Console(file=buf, width=200, color_system=None))
    r._render_log(_evt(exc_txt="Traceback (most recent call last):\n  File x"))
    out = buf.getvalue()
    assert "Traceback" in out


# --- file renderer 对 LogEvent 落空（不进 workflow.log，clean 分离）---

async def test_logevent_not_written_to_workflow_log():
    writer = AsyncMock()
    r = FileLogRenderer(writer)
    await r.render(_evt(level="ERROR"))
    writer.write.assert_not_awaited()
