import logging
from pathlib import Path

import pytest

from supernova_core.logging.temporalio_redirect import install_temporalio_log_redirect


_LOGGER_NAME = "temporalio.activity"
# install_temporalio_log_redirect 管理的 logger 集合(temporalio.activity 现有 +
# temporalio.worker 子树新增,后者覆盖 _activity :315/:521 执行边界 DEBUG)。
_MANAGED_LOGGERS = ("temporalio.activity", "temporalio.worker")


@pytest.fixture(autouse=True)
def _restore_temporalio_loggers():
    """Snapshot and restore every managed temporalio logger's state.

    ``logging.getLogger(name)`` returns the same global logger object across
    tests, so handlers attached by ``install_temporalio_log_redirect`` and the
    ``propagate=False`` toggle would leak into subsequent tests (both within
    this module and across the wider suite). We snapshot before and restore
    after so each test starts from a clean, propagation-on, handler-less
    state. Covers all managed loggers (temporalio.activity + temporalio.worker
    subtree) so the worker-coverage extension cannot leak either.
    """
    saved = {
        name: (
            list(logging.getLogger(name).handlers),
            logging.getLogger(name).propagate,
            logging.getLogger(name).level,
        )
        for name in _MANAGED_LOGGERS
    }
    try:
        yield
    finally:
        for name in _MANAGED_LOGGERS:
            logger = logging.getLogger(name)
            orig_handlers, orig_propagate, orig_level = saved[name]
            for h in list(logger.handlers):
                if h not in orig_handlers:
                    logger.removeHandler(h)
                    h.close()
            for h in orig_handlers:
                if h not in logger.handlers:
                    logger.addHandler(h)
            logger.propagate = orig_propagate
            logger.setLevel(orig_level)


def test_failure_record_goes_to_file_not_stderr(tmp_path, capsys):
    log_path = tmp_path / "activity_failures.log"
    install_temporalio_log_redirect(log_path)

    # Simulate a configured root logger with a stderr handler. With such a
    # handler present, a propagated record WOULD reach stderr; this test only
    # stays clean if ``propagate=False`` blocks the walk toward root. (Without
    # a root handler, Python's lastResort never fires because our WARNING-level
    # FileHandler already handled the record — so a bare assert on stderr would
    # pass regardless of the propagate setting and could not detect a
    # regression removing ``propagate=False``.)
    import sys
    root_stderr_handler = logging.StreamHandler(sys.stderr)
    root_logger = logging.getLogger()
    root_logger.addHandler(root_stderr_handler)
    try:
        logger = logging.getLogger(_LOGGER_NAME)
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logger.warning("Completing activity as failed", exc_info=True)

        captured = capsys.readouterr()
        assert "Traceback" not in captured.err              # not on terminal
        assert "Traceback" in log_path.read_text()          # into file
        assert "Completing activity as failed" in log_path.read_text()
    finally:
        root_logger.removeHandler(root_stderr_handler)


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


def test_worker_activity_debug_goes_to_file_when_debug_env(tmp_path, monkeypatch):
    """temporalio.worker._activity 的执行边界 DEBUG(:315 Running / :521 Completing)
    在 SUPERNOVA_TEMPORALIO_LOG_LEVEL=DEBUG 时进入 per-workspace 文件。

    这是 '10min 无日志空窗' 可观测性的核心: 拿到 activity 被 worker 取走执行的
    时间戳序列, 判定 attempt=1 有无被执行。默认(env 未设)不进文件。
    """
    monkeypatch.setenv("SUPERNOVA_TEMPORALIO_LOG_LEVEL", "DEBUG")
    log_path = tmp_path / "temporalio-activity.log"
    install_temporalio_log_redirect(log_path)

    logging.getLogger("temporalio.worker._activity").debug(
        "Running activity run_framework_analysis (token xyz)")

    assert "Running activity run_framework_analysis" in log_path.read_text()


def test_worker_debug_does_not_leak_to_root_when_debug_env(tmp_path, monkeypatch, capsys):
    """propagate=False 截断(spec 不变量 I2): env=DEBUG 时 worker DEBUG record
    进文件, 但不向上传播到 root —— 不污染 display 流 / 终端。

    若有人误删 propagate=False, worker DEBUG 会经 LogBusHandler 刷屏 live display;
    本测试用一个 root stderr handler 探测传播是否被截断。
    """
    import sys
    monkeypatch.setenv("SUPERNOVA_TEMPORALIO_LOG_LEVEL", "DEBUG")
    log_path = tmp_path / "temporalio-activity.log"
    install_temporalio_log_redirect(log_path)

    root_stderr_handler = logging.StreamHandler(sys.stderr)
    root_logger = logging.getLogger()
    root_logger.addHandler(root_stderr_handler)
    try:
        logging.getLogger("temporalio.worker._activity").debug("Running activity leak_check")

        captured = capsys.readouterr()
        assert "leak_check" in log_path.read_text()        # 进文件
        assert "leak_check" not in captured.err            # 不进终端(截断到 root)
    finally:
        root_logger.removeHandler(root_stderr_handler)


def test_worker_activity_debug_filtered_when_env_unset(tmp_path, monkeypatch):
    """默认(env 未设)→ handler WARNING, temporalio.worker._activity 的 DEBUG 不进文件。

    零回归不变量 I1: env 未设时行为与改前完全一致(worker 子树默认也不泄 DEBUG)。
    """
    monkeypatch.delenv("SUPERNOVA_TEMPORALIO_LOG_LEVEL", raising=False)
    log_path = tmp_path / "temporalio-activity.log"
    install_temporalio_log_redirect(log_path)

    logging.getLogger("temporalio.worker._activity").debug("should_not_appear_default")

    assert "should_not_appear_default" not in log_path.read_text()


def test_invalid_env_level_falls_back_to_warning(tmp_path, monkeypatch):
    """env 非法值 → 回落 WARNING, 不抛(spec §7); handler 级别 == WARNING, DEBUG 被滤。"""
    monkeypatch.setenv("SUPERNOVA_TEMPORALIO_LOG_LEVEL", "BOGUS")
    log_path = tmp_path / "temporalio-activity.log"
    install_temporalio_log_redirect(log_path)              # 不抛

    logging.getLogger("temporalio.worker._activity").debug("should_not_appear_bogus")
    assert "should_not_appear_bogus" not in log_path.read_text()
    handlers = [h for h in logging.getLogger("temporalio.worker").handlers
                if isinstance(h, logging.FileHandler)]
    assert handlers and handlers[0].level == logging.WARNING
