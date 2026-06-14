from shannon_whitebox.audit.session_registry import (
    get_audit_session, set_audit_session, clear_audit_session, NullAuditSession,
)


async def test_default_is_null_and_safe():
    clear_audit_session()
    s = get_audit_session()
    assert isinstance(s, NullAuditSession)
    # All methods are no-ops and safe to call without initialize()
    await s.start_agent("recon", "p", attempt=1)
    await s.log_event("tool_start", {"toolName": "Read"})
    await s.log_phase_start("recon")
    await s.end_agent("recon", None)


async def test_set_then_get_returns_instance():
    clear_audit_session()
    sentinel = object()
    set_audit_session(sentinel)  # type: ignore[arg-type]
    assert get_audit_session() is sentinel
    clear_audit_session()
