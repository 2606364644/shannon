"""run_report_agent 路径对齐测试。

run_report_agent 必须把 deliverables_path 指向 deliverables/blackbox/，与
assemble_report / finalize_report 一致（它们用 bb = blackbox_dir(deliverables)）。

否则 report-executive prompt 在顶层 deliverables/ 找不到 assemble 拼好的报告
（拼好的在 blackbox/ 子目录），agent 发散自建、写错位置，validator 在顶层
校验也找不到 → Missing deliverable（OUTPUT_VALIDATION_FAILED 重试到死）。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from supernova_core.models.metrics import AgentMetrics, SessionMetadata
from supernova_core.audit.session import AuditSession
from supernova_core.audit.session_registry import set_audit_session, clear_audit_session
from supernova_core.utils.paths import BLACKBOX_SUBDIR
from supernova_blackbox.pipeline.shared import BlackboxActivityInput


@pytest.mark.asyncio
async def test_run_report_agent_targets_blackbox_subdir(tmp_path, monkeypatch):
    """deliverables_path/repo_path 必须落 deliverables/blackbox/（非顶层 deliverables/）。"""
    from supernova_blackbox.pipeline import activities

    # web 模式：workspace_path = scan_dir，_get_deliverables_path 走它
    scan_dir = tmp_path / "workspaces" / "__legacy__" / "scans" / "repo-20260801"
    scan_dir.mkdir(parents=True)
    inp = BlackboxActivityInput(
        web_url="https://example.com",
        workspace_path=str(scan_dir),
        deliverables_subdir="deliverables",
    )

    meta = SessionMetadata(id="s1", web_url="https://example.com", output_path=str(tmp_path))
    session = AuditSession(meta)
    await session.initialize()
    set_audit_session(session)

    mock_executor = MagicMock()
    mock_executor.execute = AsyncMock(
        return_value=AgentMetrics(duration_ms=1, cost_usd=0.0, num_turns=1, model="m")
    )
    monkeypatch.setattr("temporalio.activity.info", lambda: MagicMock(attempt=1))

    try:
        with patch("supernova_blackbox.pipeline.activities.AgentExecutor",
                   return_value=mock_executor):
            await activities.run_report_agent(inp)

        kwargs = mock_executor.execute.call_args.kwargs
        expected_bb = scan_dir / "deliverables" / BLACKBOX_SUBDIR
        # deliverables_path 必须落 blackbox/ 子目录（对齐 assemble_report 的 bb）
        assert kwargs["deliverables_path"] == str(expected_bb), (
            "run_report_agent 的 deliverables_path 必须指向 deliverables/blackbox/，"
            "否则 report-executive 在顶层找不到 assemble 拼好的报告 + validator 校验顶层 → Missing deliverable"
        )
        # repo_path 同步（report agent 工作目录也在 blackbox/）
        assert kwargs["repo_path"] == str(expected_bb)
        # blackbox/ 子目录必须已被创建（agent 要在里面写报告）
        assert expected_bb.is_dir(), "blackbox/ 子目录须 mkdir，agent 才能 Write 进去"
    finally:
        clear_audit_session()
        await session.close()
