"""SessionToolAuditLogger 在 session 未绑定（NullAuditSession）时不得崩溃。

回归（2026-07-22，scan NodeGoat_1784743576）：scan 走到 finalize / worker 清理
``clear_audit_session()`` 后，temporal 仍在 worker 上重试被 rate-limit 推迟的
vuln agent activity → ``get_audit_session()`` 返回 ``NullAuditSession`` →
``SessionToolAuditLogger.__init__`` 访问私有 ``session._meta``（NullAuditSession
按设计只镜像公开方法、不暴露私有属性）→ AttributeError → 确定性崩溃、retry 无效、
activity_failures.log 噪音（18:30:46+ 5 次同源崩溃）。

审计是日志层：session 缺失时应 no-op，业务逻辑（executor.execute）继续，而非崩在
agent 启动前（``run_agent`` line 186，在 try 块之前，连 except 都进不去）。
"""
import pytest

from supernova_core.audit.session_registry import NullAuditSession
from supernova_core.audit.session_tool_audit_logger import SessionToolAuditLogger


@pytest.mark.asyncio
async def test_session_tool_audit_logger_tolerates_null_session():
    """NullAuditSession 传入 __init__ 不抛 AttributeError，审计方法全部 no-op。"""
    # __init__ 不抛 AttributeError（回归点：原直接访问 session._meta）
    logger = SessionToolAuditLogger(NullAuditSession(), "xss-vuln", attempt=1)

    # 全部审计方法 no-op（NullAuditSession + no-op agent logger）
    await logger.initialize()
    await logger.log_tool_start("read_file", {"path": "/x"})
    await logger.log_tool_end({"ok": True})
    await logger.log_assistant_turn(1, "content")
    await logger.log_error("boom", turn_count=1, duration_ms=10)
    await logger.close(success=False, duration_ms=100)
