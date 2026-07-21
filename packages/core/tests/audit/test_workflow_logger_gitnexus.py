import asyncio
from supernova_core.display.events import GitnexusLlmEvent
from supernova_core.audit.session_registry import NullAuditSession


def test_null_session_log_gitnexus_progress_is_noop():
    # NullAuditSession must expose the method (no AttributeError) and be awaitable.
    asyncio.run(
        NullAuditSession().log_gitnexus_progress(
            "sink-discovery", "hit", 5, 87, 1, "'x' @ f.py:1 slot=a"))


def test_workflow_logger_dispatches_gitnexus_event(monkeypatch):
    from supernova_core.audit.workflow_logger import WorkflowLogger
    dispatched = []
    wl = WorkflowLogger.__new__(WorkflowLogger)   # bypass __init__ (needs meta)
    wl._dispatcher = type("D", (), {"dispatch": staticmethod(
        lambda ev: dispatched.append(ev) or asyncio.sleep(0))})()
    asyncio.run(
        wl.log_gitnexus_progress("chain-verdict", "summary", 34, 34, 5, "5 vulnerable"))
    assert isinstance(dispatched[0], GitnexusLlmEvent)
    assert dispatched[0].kind == "summary" and dispatched[0].phase == "chain-verdict"


def test_workflow_logger_no_dispatcher_is_safe():
    from supernova_core.audit.workflow_logger import WorkflowLogger
    wl = WorkflowLogger.__new__(WorkflowLogger)
    wl._dispatcher = None
    asyncio.run(
        wl.log_gitnexus_progress("sink-discovery", "progress", 10, 87, 3))  # no raise
