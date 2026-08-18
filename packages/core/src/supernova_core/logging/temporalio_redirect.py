"""Redirect temporalio's worker-scope logging to a per-workspace file.

Two concerns are served by diverting these loggers off the terminal and into a
file, controlled by ``SUPERNOVA_TEMPORALIO_LOG_LEVEL`` (default ``WARNING``):

1. **Noise suppression (default behavior, zero-regression).** temporalio 1.27.2
   logs every activity failure with a full chained traceback via
   ``temporalio.activity.logger.warning("Completing activity as failed", exc_info=True)``
   (worker/_activity.py:474). With no logging config in supernova that record hits
   stderr via root's lastResort handler — scary, redundant noise next to our own
   clean [ERROR] line. We divert it to a file and keep the terminal clean.

2. **Worker observability (opt-in via env).** temporalio's activity *execution
   boundary* is logged at DEBUG on a *different* logger — ``temporalio.worker._activity``
   (worker/_activity.py:315 ``Running activity <type>``, :521 ``Completing activity``).
   By default these DEBUG records are filtered (root is INFO); the worker layer
   (schedule→poll→execute) is a black box, which is why a worker that fails to poll
   an activity task for minutes produces *zero* visible log. Setting
   ``SUPERNOVA_TEMPORALIO_LOG_LEVEL=DEBUG`` surfaces these records into the same file,
   so the next reproduction of a "10-min log gap" shows whether the worker ever took
   the activity task (``Running activity`` present?) and what else it was running.

Managed loggers (each gets its own FileHandler on *log_path*):

- ``temporalio.activity`` — failure tracebacks (WARNING) + heartbeats (DEBUG).
- ``temporalio.worker`` — covers the ``_activity`` / ``_workflow`` / ``_worker``
  subtree; attaching here + ``propagate=False`` captures child DEBUG records
  (e.g. ``temporalio.worker._activity``) and truncates their walk to root.

Two mechanisms keep the terminal clean, only one of which is currently load-bearing:

- The ``SUPERNOVA_TEMPORALIO_LOG_LEVEL``-gated ``FileHandler`` attached to each managed
  logger is what *currently* suppresses ``lastResort`` on stderr: Python's
  lastResort only fires for an unhandled record, and because this handler processes
  the record it is no longer unhandled — so lastResort never engages, regardless of
  ``propagate``.
- ``propagate=False`` is a *defense in depth*: it blocks the record from walking up
  to root, so a future root-level stderr handler (e.g. an app that configures the
  root logger, or the LogBusHandler that feeds the live display) would not
  double-emit the traceback / spew DEBUG worker noise into the display stream.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

_LOGGER_NAME = "temporalio.activity"
# 管理的 logger 集合: temporalio.activity(现有, failure trace) + temporalio.worker
# 子树(新增, 覆盖 _activity :315/:521 执行边界 DEBUG)。父 logger temporalio.worker
# 挂 handler + propagate=False 即截断整子树(_activity/_workflow/_worker)到 root 的传播。
_MANAGED_LOGGERS = ("temporalio.activity", "temporalio.worker")

_DEFAULT_LEVEL = "WARNING"


def _resolve_level() -> int:
    """``SUPERNOVA_TEMPORALIO_LOG_LEVEL`` → logging level int (default WARNING, 零回归).

    合法 level 名(DEBUG/INFO/WARNING/ERROR/CRITICAL)→ 对应 int; 非法值回落 WARNING
    + warning, 绝不抛(logging 故障不上行成扫描故障)。
    """
    raw = os.getenv("SUPERNOVA_TEMPORALIO_LOG_LEVEL", _DEFAULT_LEVEL).upper()
    level = logging.getLevelName(raw)
    if not isinstance(level, int):
        logging.getLogger(__name__).warning(
            "SUPERNOVA_TEMPORALIO_LOG_LEVEL=%r 不是合法 level 名, 回落 %s",
            raw, _DEFAULT_LEVEL,
        )
        return logging.WARNING
    return level


def _resolved_handler_path(h: logging.FileHandler) -> Path | None:
    """FileHandler 的 resolved baseFilename；resolve 失败返 None（比较时视为不匹配）。"""
    try:
        return Path(h.baseFilename).resolve()
    except OSError:
        return None


def _has_file_handler_on(logger: logging.Logger, target: Path) -> bool:
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler):
            p = _resolved_handler_path(h)
            if p is not None and p == target:
                return True
    return False


def install_temporalio_log_redirect(log_path: Path) -> Path:
    """Divert every managed temporalio logger's records to *log_path*; suppress from terminal.

    - 对每个被管 logger(``_MANAGED_LOGGERS``)挂一个 ``FileHandler``, 级别由
      ``SUPERNOVA_TEMPORALIO_LOG_LEVEL`` 决定(默认 ``WARNING`` = 现状零回归, 只收 failure
      trace; ``DEBUG`` 收 activity 执行边界日志, 排 10min 空窗之用)。
    - ``propagate=False`` → 截断到 root LogBus, DEBUG 不污染 display 流(终端干净)。
    - ``logger.setLevel(DEBUG)`` → logger 不滤, handler 按 env 决定(不丢任何 record)。
    - 幂等: 同 logger 上同路径 FileHandler 不重复挂; 指向其它路径的旧 FileHandler
      (上一会话残留)被摘除 + close(防双写/句柄泄漏)。
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    target = log_path.resolve()
    level = _resolve_level()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s")

    for name in _MANAGED_LOGGERS:
        logger = logging.getLogger(name)
        # 摘掉指向其它路径的旧 FileHandler（上一会话残留）：不摘则同一条 record 被
        # 新旧 handler 双写（两个会话目录的 activity_failures.log 内容相同）且旧文件
        # 句柄泄漏（2026-08-18 实测两目录 md5 相同坐实）。同路径幂等跳过保留。
        for h in list(logger.handlers):
            if not isinstance(h, logging.FileHandler):
                continue
            p = _resolved_handler_path(h)
            if p is not None and p != target:
                logger.removeHandler(h)
                h.close()
        if _has_file_handler_on(logger, target):
            continue  # already installed on this path for this logger
        handler = logging.FileHandler(log_path)
        handler.setLevel(level)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.propagate = False
        logger.setLevel(logging.DEBUG)   # don't filter at logger level; handler decides

    return log_path
