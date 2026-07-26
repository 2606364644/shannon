"""P3c 阶段 3：AuditSession 按 workflow_id 隔离（多 scan 并发不串台）。"""
from supernova_core.audit.session_registry import (
    set_audit_session,
    get_audit_session,
    get_audit_session_for,
    clear_audit_session,
    NullAuditSession,
    _resolve_wf_id,
    _SESSIONS,
    _current_wf_id,
)


def setup_function():
    # 每个 test 前 clean slate：清注册表 + 重置 contextvar（防上一个 test 残留）。
    _SESSIONS.clear()
    _current_wf_id.set(None)


def test_resolve_wf_id_falls_back_to_cli_without_activity_context():
    """非 activity 线程 → '_cli'。"""
    assert _resolve_wf_id() == "_cli"
    assert _resolve_wf_id("explicit-wf") == "explicit-wf"  # 显式优先


def test_set_get_isolated_per_workflow_id():
    sA, sB = object(), object()
    set_audit_session(sA, workflow_id="wf-A")
    set_audit_session(sB, workflow_id="wf-B")
    assert get_audit_session_for("wf-A") is sA
    assert get_audit_session_for("wf-B") is sB
    assert get_audit_session_for("wf-A") is not sB


def test_clear_one_does_not_affect_other():
    set_audit_session(object(), workflow_id="wf-A")
    set_audit_session(object(), workflow_id="wf-B")
    clear_audit_session(workflow_id="wf-A")
    assert isinstance(get_audit_session_for("wf-A"), NullAuditSession)
    assert not isinstance(get_audit_session_for("wf-B"), NullAuditSession)


def test_get_audit_session_for_unknown_returns_null():
    assert isinstance(get_audit_session_for("never-set"), NullAuditSession)


def test_get_audit_session_without_activity_context_uses_cli_key():
    """非 activity 线程 get → 查 '_cli' key（CLI 兼容）。"""
    s = object()
    set_audit_session(s, workflow_id="_cli")
    assert get_audit_session() is s
