import asyncio

from shannon_core.audit.workflow_logger import WorkflowLogger
from shannon_core.models.errors import ErrorCode, PentestError


class _SpyDispatcher:
    def __init__(self):
        self.events = []

    async def dispatch(self, event):
        self.events.append(event)


def _make_logger():
    class _Meta:
        repo_path = "/tmp/repo"
        workspace_name = "ws"
        session_id = "s"
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = _SpyDispatcher()
    wl._meta = _Meta()
    wl._activity_failure_log_path = None
    return wl


def test_retryable_agent_execution_uses_models_classification():
    wl = _make_logger()
    err = PentestError(
        "Agent xss-vuln execution failed", "validation",
        retryable=True, error_code=ErrorCode.AGENT_EXECUTION_FAILED,
    )
    asyncio.run(wl.log_error(err, context="xss-vuln", attempt=2, max_attempts=5))
    ev = wl._dispatcher.events[-1]
    assert ev.classified == "AgentExecutionError"      # 同源（非 TransientError）
    assert ev.display_retryable is True
    assert ev.attempt == 2 and ev.max_attempts == 5


def test_non_retryable_auth():
    wl = _make_logger()
    err = PentestError("bad key", "auth", retryable=False, error_code=ErrorCode.AUTH_FAILED)
    asyncio.run(wl.log_error(err))
    ev = wl._dispatcher.events[-1]
    assert ev.classified == "AuthenticationError"
    assert ev.display_retryable is False
