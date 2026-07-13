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


# --- 诊断行 dim 降级 + 保留级别色（markup 断言）---

@pytest.mark.parametrize("level,color", [
    ("ERROR", "bold red"),
    ("CRITICAL", "bold red"),
    ("WARNING", "yellow"),
    ("INFO", "cyan"),
    ("DEBUG", ""),
])
def test_logevent_level_color_markup(level, color):
    """诊断 log 行 dim 降级，且保留级别色调：dim cyan / dim yellow / dim bold red / 纯 dim(DEBUG)。"""
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
    joined = " ".join(captured)
    assert "dim" in joined, f"level={level} 诊断行应 dim 降级，实际 printed={captured}"
    if color:
        assert color in joined, f"level={level} 应保留级别色 {color}，实际 printed={captured}"


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


# --- 续行缩进 + InfoEvent 不受 dim 影响（spec 2026-07-14 模块2/3）---

def test_logevent_long_message_continuation_indented():
    """长诊断消息续行缩进到 LOG_INDENT 列，不再 Rich 硬换行顶格。"""
    from shannon_core.display.formatters import LOG_INDENT
    buf = StringIO()
    r = RichConsoleRenderer(Console(file=buf, width=60, color_system=None))
    long_msg = "Discovered 34 security files: " + ("config=10, " * 20)
    r._render_log(_evt(level="INFO", message=long_msg))
    lines = buf.getvalue().splitlines()
    assert len(lines) > 1  # 触发换行
    assert lines[1].startswith(" " * LOG_INDENT), f"续行应缩进 {LOG_INDENT} 列: {lines[1]!r}"


async def test_info_event_render_not_dim_keeps_bright():
    """InfoEvent(显式用户消息)保持亮色，不受诊断 LogEvent 的 dim 降级影响。"""
    from unittest.mock import MagicMock
    from shannon_core.display.events import InfoEvent
    console = MagicMock()
    await RichConsoleRenderer(console=console).render(
        InfoEvent(timestamp="t", category="INFO", message="hi", level="info"))
    printed = console.print.call_args.args[0]
    assert "dim" not in printed
    assert "cyan" in printed


# --- verbose 开关（spec 2026-07-14 §模块4）---

def test_logevent_verbose_0_suppresses_terminal_output(monkeypatch):
    """SHANNON_LOG_VERBOSE=0：诊断 LogEvent 不上终端（仍落 diagnostic.log，独立 renderer）。"""
    monkeypatch.setenv("SHANNON_LOG_VERBOSE", "0")
    buf = StringIO()
    r = RichConsoleRenderer(Console(file=buf, width=200, color_system=None))
    r._render_log(_evt(level="WARNING"))
    assert buf.getvalue() == "", f"verbose=0 时终端应无诊断输出: {buf.getvalue()!r}"


def test_logevent_verbose_default_shows_on_terminal(monkeypatch):
    """SHANNON_LOG_VERBOSE 未设(默认 1)：诊断 LogEvent 正常 dim 渲染到终端。"""
    monkeypatch.delenv("SHANNON_LOG_VERBOSE", raising=False)
    buf = StringIO()
    r = RichConsoleRenderer(Console(file=buf, width=200, color_system=None))
    r._render_log(_evt(level="WARNING"))
    assert "queue unreadable" in buf.getvalue()
