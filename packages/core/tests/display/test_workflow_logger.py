import pytest
from unittest.mock import AsyncMock

from supernova_core.audit.workflow_logger import WorkflowLogger
from supernova_core.display.events import InfoEvent


@pytest.mark.asyncio
async def test_log_info_dispatches_info_event():
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = AsyncMock()
    wl._activity_failure_log_path = None
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


# --- log_error category 降级(对齐 TS logger.warn: attempt 级=WARNING, 用尽/non-retryable=ERROR) ---

@pytest.mark.asyncio
async def test_log_error_retryable_in_progress_uses_warning_category():
    """retryable 且未用尽(attempt<max)→ WARNING:本次失败但 Temporal 会重试,不报 ERROR 吓人。

    对齐原始 TS createVulnValidator 的 logger.warn(shannon/session-manager.ts:143)。
    """
    from supernova_core.models.errors import PentestError, ErrorCode
    from supernova_core.display.events import ErrorEvent
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = AsyncMock()
    wl._activity_failure_log_path = None
    err = PentestError(
        "Missing exploitation queue for injection-vuln: injection_exploitation_queue.json",
        "validation", error_code=ErrorCode.OUTPUT_VALIDATION_FAILED)
    await wl.log_error(err, context="injection-vuln", attempt=1, max_attempts=8)
    event = wl._dispatcher.dispatch.await_args.args[0]
    assert isinstance(event, ErrorEvent)
    assert event.category == "WARNING"


@pytest.mark.asyncio
async def test_log_error_retryable_exhausted_uses_error_category():
    """retryable 但 attempt 已达 max(用尽)→ ERROR:最终失败,不再重试。"""
    from supernova_core.models.errors import PentestError, ErrorCode
    from supernova_core.display.events import ErrorEvent
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = AsyncMock()
    wl._activity_failure_log_path = None
    err = PentestError(
        "Missing exploitation queue for injection-vuln: injection_exploitation_queue.json",
        "validation", error_code=ErrorCode.OUTPUT_VALIDATION_FAILED)
    await wl.log_error(err, context="injection-vuln", attempt=8, max_attempts=8)
    event = wl._dispatcher.dispatch.await_args.args[0]
    assert isinstance(event, ErrorEvent)
    assert event.category == "ERROR"


@pytest.mark.asyncio
async def test_log_error_non_retryable_uses_error_category():
    """non-retryable → ERROR:本就不会重试,即最终失败。"""
    from supernova_core.models.errors import PentestError, ErrorCode
    from supernova_core.display.events import ErrorEvent
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = AsyncMock()
    wl._activity_failure_log_path = None
    err = PentestError("repo missing", "validation", error_code=ErrorCode.REPO_NOT_FOUND)
    await wl.log_error(err, context="injection-vuln")
    event = wl._dispatcher.dispatch.await_args.args[0]
    assert isinstance(event, ErrorEvent)
    assert event.category == "ERROR"


# --- SUPERNOVA_SILENT_RETRY env:attempt 级失败静默(对齐 TS 终端不渲染 attempt 级) ---

@pytest.mark.asyncio
async def test_log_error_silent_retry_env_suppresses_attempt_warning(monkeypatch):
    """SUPERNOVA_SILENT_RETRY=1 → attempt 级失败不进终端 UI(dispatch 不调用)。

    对齐 TS progress-indicator 不渲染 attempt 级(只 spinner);用尽/non-retryable 不受影响。
    """
    from supernova_core.models.errors import PentestError, ErrorCode
    monkeypatch.setenv("SUPERNOVA_SILENT_RETRY", "1")
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = AsyncMock()
    wl._activity_failure_log_path = None
    err = PentestError("Missing exploitation queue", "validation",
                       error_code=ErrorCode.OUTPUT_VALIDATION_FAILED)
    await wl.log_error(err, context="injection-vuln", attempt=1, max_attempts=8)
    wl._dispatcher.dispatch.assert_not_awaited()  # silent → 不进 UI


@pytest.mark.asyncio
async def test_log_error_silent_retry_does_not_suppress_exhausted(monkeypatch):
    """silent 只影响 attempt 级;用尽(attempt=max)→ 仍 dispatch ERROR(真失败必须可见)。"""
    from supernova_core.models.errors import PentestError, ErrorCode
    from supernova_core.display.events import ErrorEvent
    monkeypatch.setenv("SUPERNOVA_SILENT_RETRY", "1")
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = AsyncMock()
    wl._activity_failure_log_path = None
    err = PentestError("Missing exploitation queue", "validation",
                       error_code=ErrorCode.OUTPUT_VALIDATION_FAILED)
    await wl.log_error(err, context="injection-vuln", attempt=8, max_attempts=8)
    event = wl._dispatcher.dispatch.await_args.args[0]
    assert isinstance(event, ErrorEvent)
    assert event.category == "ERROR"  # 用尽 → 仍显示


@pytest.mark.asyncio
async def test_log_error_silent_retry_off_defaults_to_warning(monkeypatch):
    """env 未设 → attempt 级仍显示 WARNING(默认行为,回归保护)。"""
    from supernova_core.models.errors import PentestError, ErrorCode
    from supernova_core.display.events import ErrorEvent
    monkeypatch.delenv("SUPERNOVA_SILENT_RETRY", raising=False)
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = AsyncMock()
    wl._activity_failure_log_path = None
    err = PentestError("Missing exploitation queue", "validation",
                       error_code=ErrorCode.OUTPUT_VALIDATION_FAILED)
    await wl.log_error(err, context="injection-vuln", attempt=1, max_attempts=8)
    event = wl._dispatcher.dispatch.await_args.args[0]
    assert isinstance(event, ErrorEvent)
    assert event.category == "WARNING"
