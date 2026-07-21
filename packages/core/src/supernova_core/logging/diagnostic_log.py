"""DiagnosticLog — diagnostic.log 的 sync 写入句柄（统一日志总线）。

spec/plan: docs/superpowers/specs/2026-07-08-unified-logging-facade-design.md
        / docs/superpowers/plans/2026-07-08-unified-log-bus.md（task 2）

被两条路径共用：
- 生产线程 fallback（无 session 时 LogBusHandler.emit 直写）；
- event-loop 的 DiagnosticLogRenderer（session 活跃时经 dispatcher 落盘）。
threading.Lock 串行化两线程的写；行频低、单行 <1ms，不用 aiofiles（plan §99 YAGNI）。
"""
from __future__ import annotations

import threading
from pathlib import Path


class DiagnosticLog:
    """Single sync file handle + threading.Lock for diagnostic.log.

    Shared by the production-thread fallback path (LogBusHandler.emit when no
    session is attached) and the event-loop DiagnosticLogRenderer. The Lock
    serializes their writes; per-write flush keeps lines durable before a
    potential worker crash.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fh = open(self._path, "a", encoding="utf-8")

    @property
    def path(self) -> Path:
        return self._path

    def write_sync(self, line: str) -> None:
        """Append one pre-formatted line (caller supplies the trailing \\n)."""
        with self._lock:
            if self._fh is None:
                return
            self._fh.write(line)
            self._fh.flush()

    def close(self) -> None:
        """Idempotent close — safe to call after the fallback/renderer is done."""
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def format_diagnostic_line(event) -> str:
    """Format a LogEvent as a diagnostic.log block (line + optional exc_txt).

    Aligns with ``setup._FORMAT``'s ``[%(asctime)s] [%(levelname)5s] %(name)s:
    %(message)s`` (level right-aligned to 5). Shared by the production-thread
    fallback (``LogBus.write_fallback``) and the event-loop DiagnosticLogRenderer
    so both paths emit identical lines.
    """
    line = f"[{event.timestamp}] [{event.level:>5}] {event.logger_name}: {event.message}\n"
    if getattr(event, "exc_txt", None):
        line += f"{event.exc_txt}\n"
    return line
