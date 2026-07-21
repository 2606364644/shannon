from unittest import mock

from supernova_core.audit.display_lifecycle import run_with_display
from supernova_core.models.metrics import SessionMetadata


async def test_rich_mode_constructs_transient_live(tmp_path):
    """Live must be transient so the status line is erased on exit and the
    SummaryEvent (printed above the live region) is the final visible output."""
    meta = SessionMetadata(id="x", web_url=None, output_path=str(tmp_path))
    with mock.patch("rich.live.Live") as live_cls, \
         mock.patch("supernova_core.audit.display_lifecycle.AuditSession") as session_cls:
        session_cls.return_value.initialize = mock.AsyncMock()
        session_cls.return_value.close = mock.AsyncMock()
        async with run_with_display(meta, use_rich=True) as session:
            assert session is session_cls.return_value
    assert live_cls.called
    assert live_cls.call_args.kwargs.get("transient") is True


async def test_non_rich_mode_does_not_construct_live(tmp_path):
    meta = SessionMetadata(id="x", web_url=None, output_path=str(tmp_path))
    with mock.patch("rich.live.Live") as live_cls, \
         mock.patch("supernova_core.audit.display_lifecycle.AuditSession") as session_cls:
        session_cls.return_value.initialize = mock.AsyncMock()
        session_cls.return_value.close = mock.AsyncMock()
        async with run_with_display(meta, use_rich=False):
            pass
    assert not live_cls.called
