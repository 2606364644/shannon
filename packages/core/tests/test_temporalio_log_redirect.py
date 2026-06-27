import logging
from pathlib import Path

import pytest

from shannon_core.logging.temporalio_redirect import install_temporalio_log_redirect


_LOGGER_NAME = "temporalio.activity"


@pytest.fixture(autouse=True)
def _restore_temporalio_activity_logger():
    """Snapshot and restore the temporalio.activity logger state.

    ``logging.getLogger(name)`` returns the same global logger object across
    tests, so handlers attached by ``install_temporalio_log_redirect`` and the
    ``propagate=False`` toggle would leak into subsequent tests (both within
    this module and across the wider suite). We snapshot before and restore
    after so each test starts from a clean, propagation-on, handler-less
    state.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    saved_handlers = list(logger.handlers)
    saved_propagate = logger.propagate
    saved_level = logger.level
    try:
        yield
    finally:
        # Restore prior state, then drop anything we added.
        for h in list(logger.handlers):
            if h not in saved_handlers:
                logger.removeHandler(h)
        # Re-add originals in case they were removed.
        for h in logger.handlers:
            pass  # originals still present
        for h in saved_handlers:
            if h not in logger.handlers:
                logger.addHandler(h)
        logger.propagate = saved_propagate
        logger.setLevel(saved_level)


def test_failure_record_goes_to_file_not_stderr(tmp_path, capsys):
    log_path = tmp_path / "activity_failures.log"
    install_temporalio_log_redirect(log_path)

    logger = logging.getLogger(_LOGGER_NAME)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logger.warning("Completing activity as failed", exc_info=True)

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err              # not on terminal
    assert "Traceback" in log_path.read_text()          # into file
    assert "Completing activity as failed" in log_path.read_text()


def test_debug_records_filtered_out_of_file(tmp_path):
    log_path = tmp_path / "activity_failures.log"
    install_temporalio_log_redirect(log_path)
    logging.getLogger(_LOGGER_NAME).debug("heartbeat noise")
    assert "heartbeat noise" not in log_path.read_text()   # handler level=WARNING


def test_install_is_idempotent(tmp_path):
    log_path = tmp_path / "activity_failures.log"
    install_temporalio_log_redirect(log_path)
    install_temporalio_log_redirect(log_path)
    handlers = [h for h in logging.getLogger(_LOGGER_NAME).handlers
                if isinstance(h, logging.FileHandler)]
    assert len(handlers) == 1                              # not re-added
    # And it is the handler pointing at the resolved target path.
    assert all(Path(h.baseFilename).resolve() == log_path.resolve() for h in handlers)
