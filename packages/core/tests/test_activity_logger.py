from unittest.mock import patch, MagicMock

from shannon_core.logging.activity_logger import (
    ActivityLogger,
    ConsoleActivityLogger,
    TemporalActivityLogger,
    create_activity_logger,
)


def test_activity_logger_is_abstract():
    """ActivityLogger cannot be instantiated directly."""
    import abc
    assert abc.ABC in ActivityLogger.__bases__


def test_console_activity_logger_info():
    """ConsoleActivityLogger delegates to stdlib logging."""
    logger = ConsoleActivityLogger()
    with patch.object(logger._logger, "info") as mock_info:
        logger.info("test message", key="value")
        mock_info.assert_called_once_with("test message", extra={"key": "value"})


def test_console_activity_logger_warn():
    logger = ConsoleActivityLogger()
    with patch.object(logger._logger, "warning") as mock_warn:
        logger.warn("warning msg", code=404)
        mock_warn.assert_called_once_with("warning msg", extra={"code": 404})


def test_console_activity_logger_error():
    logger = ConsoleActivityLogger()
    with patch.object(logger._logger, "error") as mock_error:
        logger.error("error msg")
        mock_error.assert_called_once_with("error msg", extra={})


def test_create_activity_logger_returns_console_outside_temporal():
    """Without a Temporal activity context, factory returns ConsoleActivityLogger."""
    # When running tests, we're outside a Temporal activity context
    # So create_activity_logger should return ConsoleActivityLogger
    logger = create_activity_logger()
    assert isinstance(logger, ConsoleActivityLogger)


def test_create_activity_logger_returns_temporal_inside_activity():
    """When inside a Temporal activity context, factory returns TemporalActivityLogger."""
    mock_activity = MagicMock()
    mock_activity.info.return_value = None

    with patch("temporalio.activity.info") as mock_info:
        mock_info.return_value = MagicMock()  # simulate being inside activity
        logger = create_activity_logger()
        assert isinstance(logger, TemporalActivityLogger)


def test_temporal_activity_logger_info():
    """TemporalActivityLogger delegates to temporalio.activity.logger."""
    logger = TemporalActivityLogger()
    with patch("temporalio.activity.logger") as mock_logger:
        logger.info("test msg", agent="recon")
        mock_logger.info.assert_called_once_with("test msg", extra={"agent": "recon"})


def test_temporal_activity_logger_warn():
    logger = TemporalActivityLogger()
    with patch("temporalio.activity.logger") as mock_logger:
        logger.warn("warn msg")
        mock_logger.warning.assert_called_once_with("warn msg", extra={})


def test_temporal_activity_logger_error():
    logger = TemporalActivityLogger()
    with patch("temporalio.activity.logger") as mock_logger:
        logger.error("error msg")
        mock_logger.error.assert_called_once_with("error msg", extra={})
