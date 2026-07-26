"""P3c 阶段 3：CLI 路径 set_audit_session 显式 workflow_id 与 activity 内 get 匹配。

Task 1 dict 化后,CLI 在 workflow 启动前(非 activity context)set_audit_session 默认落
'_cli';而 activity 内 get_audit_session() 经 activity.info().workflow_id 查真实 wf id →
失配 → activity 拿 NullAuditSession。Task 4 让 CLI 显式传 workflow_id(= start_workflow
的 id)使两者匹配。本测试用 mock temporalio.activity.info 模拟 activity 体内路径。
"""
from unittest.mock import patch, MagicMock

from supernova_core.audit.session_registry import (
    set_audit_session,
    clear_audit_session,
    get_audit_session,
    NullAuditSession,
    _SESSIONS,
)


def setup_function():
    _SESSIONS.clear()


def test_cli_explicit_workflow_id_matches_activity_get():
    """CLI set_audit_session(session, workflow_id=W) → activity 内 get_audit_session()
    (activity.info().workflow_id=W) 拿到同一 session(不再 NullAuditSession)。"""
    session = object()
    wf_id = "host_20260727-120000"
    set_audit_session(session, workflow_id=wf_id)  # CLI 路径显式传
    fake_info = MagicMock()
    fake_info.workflow_id = wf_id
    with patch("temporalio.activity.info", return_value=fake_info):
        assert get_audit_session() is session  # activity 内 get 命中
    clear_audit_session(workflow_id=wf_id)


def test_cli_set_without_workflow_id_does_not_match_activity_get():
    """反证(Task4 要修的 bug 的回归锚点):CLI 不传 workflow_id(落 '_cli') →
    activity 内 get(真实 wf id)拿 NullAuditSession(失配)。"""
    session = object()
    wf_id = "host_20260727-120000"
    set_audit_session(session)  # 不传 → '_cli'(CLI 默认,Task4 前的旧行为)
    fake_info = MagicMock()
    fake_info.workflow_id = wf_id
    with patch("temporalio.activity.info", return_value=fake_info):
        assert isinstance(get_audit_session(), NullAuditSession)  # 失配 → Null
    clear_audit_session()  # 清 '_cli'
