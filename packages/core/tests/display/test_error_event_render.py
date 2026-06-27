"""Task 2 (Part C): ErrorEvent retry/detail fields + renderer rendering.

Verifies the new ``attempt``/``max_attempts``/``detail_path`` fields render as
``将重试 N/M`` / ``不可重试`` plus a ``详细堆栈见 …`` suffix in both renderers.
"""
from io import StringIO

from rich.console import Console

from shannon_core.display.events import ErrorEvent
from shannon_core.display.file_renderer import FileLogRenderer
from shannon_core.display.rich_renderer import RichConsoleRenderer


def _evt(**kw):
    base = dict(
        timestamp="2026-06-27 20:00:00",
        category="ERROR",
        error_type="PentestError",
        message="Agent xss-vuln execution failed",
        context="xss-vuln",
        classified="AgentExecutionError",
        display_retryable=True,
    )
    base.update(kw)
    return ErrorEvent(**base)


def test_rich_retryable_shows_attempt_max():
    buf = StringIO()
    r = RichConsoleRenderer(Console(file=buf, width=200, color_system=None))
    r._render_error(_evt(attempt=2, max_attempts=5))
    out = buf.getvalue()
    assert "AgentExecutionError" in out
    assert "将重试 2/5" in out
    assert "non-retryable" not in out


def test_rich_non_retryable():
    buf = StringIO()
    r = RichConsoleRenderer(Console(file=buf, width=200, color_system=None))
    r._render_error(_evt(display_retryable=False, classified="AuthenticationError"))
    out = buf.getvalue()
    assert "不可重试" in out


def test_rich_detail_path_suffix():
    buf = StringIO()
    r = RichConsoleRenderer(Console(file=buf, width=200, color_system=None))
    r._render_error(_evt(detail_path="logs/activity_failures.log"))
    assert "详细堆栈见 logs/activity_failures.log" in buf.getvalue()


def test_file_retryable_and_detail():
    # _error is a pure str-returning fn; pass None as writer (never invoked).
    r = FileLogRenderer(writer=None)
    line = r._error(_evt(attempt=2, max_attempts=5, detail_path="logs/activity_failures.log"))
    assert "[AgentExecutionError · 将重试 2/5]" in line
    assert "详细堆栈见 logs/activity_failures.log" in line
