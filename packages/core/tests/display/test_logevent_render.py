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

import re

from supernova_core.display.events import LogEvent, StepEvent
from supernova_core.display.file_renderer import FileLogRenderer
from supernova_core.display.rich_renderer import RichConsoleRenderer


def _evt(level="WARNING", **kw):
    base = dict(
        timestamp="2026-07-08 10:00:00",
        category=level,
        logger_name="supernova_core.code_index",
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
    assert "supernova_core.code_index" in out
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
    from supernova_core.display.formatters import LOG_INDENT
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
    from supernova_core.display.events import InfoEvent
    console = MagicMock()
    await RichConsoleRenderer(console=console).render(
        InfoEvent(timestamp="t", category="INFO", message="hi", level="info"))
    printed = console.print.call_args.args[0]
    assert "dim" not in printed
    assert "cyan" in printed


# --- verbose 开关（spec 2026-07-14 §模块4）---

def test_logevent_verbose_0_suppresses_terminal_output(monkeypatch):
    """SUPERNOVA_LOG_VERBOSE=0：诊断 LogEvent 不上终端（仍落 diagnostic.log，独立 renderer）。"""
    monkeypatch.setenv("SUPERNOVA_LOG_VERBOSE", "0")
    buf = StringIO()
    r = RichConsoleRenderer(Console(file=buf, width=200, color_system=None))
    r._render_log(_evt(level="WARNING"))
    assert buf.getvalue() == "", f"verbose=0 时终端应无诊断输出: {buf.getvalue()!r}"


def test_logevent_verbose_default_shows_on_terminal(monkeypatch):
    """SUPERNOVA_LOG_VERBOSE 未设(默认 1)：诊断 LogEvent 正常 dim 渲染到终端。"""
    monkeypatch.delenv("SUPERNOVA_LOG_VERBOSE", raising=False)
    buf = StringIO()
    r = RichConsoleRenderer(Console(file=buf, width=200, color_system=None))
    r._render_log(_evt(level="WARNING"))
    assert "queue unreadable" in buf.getvalue()


# --- dim 诊断行与亮色 structured event 首列对齐（2026-07-14 缩进修复）---

def _body_start_col(render_fn) -> int:
    """渲染一行（color_system=None → markup 剥离为纯文本），返回标签后正文起点列。

    '[ts] ' 公共前缀 = 22 列；其后 '标签 + 分隔空格'，再后是正文/logger_name。
    用 re.match(r'\\S+\\s+', tail) 量出 '标签区+分隔' 宽度，22 + 其 end 即正文起点。
    """
    buf = StringIO()
    r = RichConsoleRenderer(Console(file=buf, width=200, color_system=None))
    render_fn(r)
    line = buf.getvalue().splitlines()[0]
    tail = line[22:]  # 去掉 '[2026-07-08 10:00:00] '
    m = re.match(r"\S+\s+", tail)
    assert m, f"未匹配到标签区: {tail!r}"
    return 22 + m.end()


def test_log_dim_column_aligns_with_bright_step_body():
    """dim 诊断 LogEvent 的 logger_name 起点列 == 亮色 STEP body 起点列。

    修复 '浅色(dim)行整体左移 1 列'：LogEvent 前缀 tag 后曾用 1 空格分隔 logger_name，
    而 PHASE/STEP/AGENT/InfoEvent 统一用 2 空格分隔 body → dim 行少 1 列。
    """
    log_col = _body_start_col(lambda r: r._render_log(_evt(level="INFO")))
    step_col = _body_start_col(lambda r: r._render_step(StepEvent(
        timestamp="2026-07-08 10:00:00", category="STEP",
        name="precheck", phase="setup", event="start")))
    assert log_col == step_col, (
        f"dim LogEvent logger_name 起点({log_col}) 应 == 亮色 STEP body 起点({step_col})")


def test_log_dim_warning_column_aligns_with_bright_info_warning():
    """dim WARNING LogEvent 也与亮色 InfoEvent WARNING 对齐（标签 7 字符同宽场景）。"""
    from supernova_core.display.events import InfoEvent
    log_col = _body_start_col(lambda r: r._render_log(_evt(level="WARNING")))
    info_col = _body_start_col(lambda r: r._render_info(InfoEvent(
        timestamp="2026-07-08 10:00:00", category="INFO",
        message="hi", level="warning")))
    assert log_col == info_col, (
        f"dim WARNING 起点({log_col}) 应 == 亮色 InfoEvent WARNING 起点({info_col})")
