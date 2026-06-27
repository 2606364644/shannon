"""Redirect temporalio's verbose activity-failure logging to a per-workspace file.

temporalio 1.27.2 logs every activity failure with a full chained traceback via
``temporalio.activity.logger.warning("Completing activity as failed", exc_info=True)``
(worker/_activity.py:474). With no logging config in shannon-py that record hits
stderr via root's lastResort handler — scary, redundant noise next to our own clean
[ERROR] line. We divert it to a file and keep the terminal clean.

Two mechanisms keep the terminal clean, only one of which is currently load-bearing:

- The WARNING-level ``FileHandler`` attached to ``temporalio.activity`` is what
  *currently* suppresses ``lastResort`` on stderr: Python's lastResort only fires
  for an unhandled record, and because this handler processes the WARNING record it
  is no longer unhandled — so lastResort never engages, regardless of ``propagate``.
- ``propagate=False`` is a *defense in depth*: it blocks the record from walking up
  to root, so a future root-level stderr handler (e.g. an app that configures the
  root logger) would not double-emit the traceback. Without it the record would
  reach any root stderr handler via the propagation walk.
"""
from __future__ import annotations

import logging
from pathlib import Path

_LOGGER_NAME = "temporalio.activity"


def install_temporalio_log_redirect(log_path: Path) -> Path:
    """Divert ``temporalio.activity`` records to *log_path*; suppress from terminal.

    - FileHandler level=WARNING → captures failure tracebacks (logged at WARNING),
      drops DEBUG heartbeat noise. Handling the record here is what currently keeps
      it off stderr (an unhandled record is what trips lastResort).
    - ``propagate=False`` → defense in depth: stops the record from walking to a
      future root-level stderr handler that would otherwise double-emit it.
    - Idempotent: a FileHandler on the same resolved path is not re-added.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    target = log_path.resolve()
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler):
            try:
                if Path(h.baseFilename).resolve() == target:
                    return log_path  # already installed on this path
            except OSError:
                continue

    handler = logging.FileHandler(log_path)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.DEBUG)   # don't filter at logger level; handler decides
    return log_path
