import pytest
from unittest.mock import AsyncMock

from shannon_core.audit.workflow_logger import WorkflowLogger
from shannon_core.display.events import InfoEvent


@pytest.mark.asyncio
async def test_log_info_dispatches_info_event():
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = AsyncMock()
    await wl.log_info("running recon from scratch", level="warning")
    wl._dispatcher.dispatch.assert_awaited_once()
    event = wl._dispatcher.dispatch.await_args.args[0]
    assert isinstance(event, InfoEvent)
    assert event.message == "running recon from scratch"
    assert event.level == "warning"
    assert event.category == "INFO"


@pytest.mark.asyncio
async def test_log_info_noop_when_no_dispatcher():
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = None
    await wl.log_info("x")  # 不应 raise
