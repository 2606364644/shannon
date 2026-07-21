"""log_info_activity 防御性 best-effort 测试。

显示侧通道失败绝不影响扫描——尤其当 log_info_activity 在 workflow 的 except 块里被调时
（whitebox 非致命降级诊断迁移），session.log_info 抛不能替换原异常流。
"""
import pytest
from unittest.mock import AsyncMock

from supernova_blackbox.pipeline.activities import log_info_activity
from supernova_blackbox.pipeline.shared import BlackboxActivityInput


@pytest.mark.asyncio
async def test_log_info_activity_swallows_session_error(monkeypatch):
    """session.log_info 抛时 activity 不传播（best-effort）。"""
    mock_session = AsyncMock()
    mock_session.log_info = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(
        "supernova_core.audit.session_registry.get_audit_session",
        lambda: mock_session,
    )
    inp = BlackboxActivityInput(web_url="x", info_message="m", info_level="warning")
    await log_info_activity(inp)  # 不应抛
    mock_session.log_info.assert_awaited_once_with("m", "warning")
