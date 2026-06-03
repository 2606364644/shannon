import io
from unittest.mock import patch

from shannon_whitebox.cli.progress import (
    ProgressIndicator,
    NullProgressIndicator,
    create_progress_indicator,
)


def test_create_progress_indicator_returns_real_when_enabled():
    indicator = create_progress_indicator("Working...", enabled=True)
    assert isinstance(indicator, ProgressIndicator)


def test_create_progress_indicator_returns_null_when_disabled():
    indicator = create_progress_indicator("Working...", enabled=False)
    assert isinstance(indicator, NullProgressIndicator)


def test_progress_indicator_start_stop():
    indicator = ProgressIndicator("Testing...")
    indicator.start()
    assert indicator._running is True
    indicator.stop()
    assert indicator._running is False


def test_progress_indicator_finish_prints_complete():
    indicator = ProgressIndicator("Testing...")
    indicator.start()
    with patch("builtins.print") as mock_print:
        indicator.finish("All done")
        mock_print.assert_called_once_with("✓ All done")
    assert indicator._running is False


def test_progress_indicator_stop_when_not_started():
    """Calling stop() without start() should not raise."""
    indicator = ProgressIndicator("Idle")
    indicator.stop()
    assert indicator._running is False


def test_progress_indicator_start_idempotent():
    """Calling start() twice should not spawn a second thread."""
    indicator = ProgressIndicator("Double")
    indicator.start()
    thread1 = indicator._thread
    indicator.start()
    thread2 = indicator._thread
    assert thread1 is thread2
    indicator.stop()


def test_null_progress_indicator_is_noop():
    """All methods on NullProgressIndicator should execute without error."""
    indicator = NullProgressIndicator()
    indicator.start()
    indicator.stop()
    indicator.finish("done")


def test_progress_indicator_spinner_frames():
    indicator = ProgressIndicator("Loading")
    assert len(indicator._frames) == 10
    assert indicator._frames[0] == "⠋"
