# packages/whitebox/tests/test_whitebox_resume.py
import pytest

from shannon_whitebox.pipeline.whitebox_resume import WhiteboxResumeState, reconcile


@pytest.mark.parametrize("g,j,f,expected_completed,expected_aborted,expects_warning", [
    # G J F -> completed? aborted? warning?
    (True,  True,  True,  True,  False, False),   # 正常完成
    (True,  False, True,  True,  False, True),    # session 落盘晚，warn
    (True,  True,  False, False, True,  False),   # 文件被误删 -> 中止
    (True,  False, False, False, True,  False),   # G 有但文件/session 都无 -> 中止
    (False, True,  True,  False, False, True),    # session 误记 -> 重跑 + warn
    (False, True,  False, False, False, True),    # session 误记 -> 重跑 + warn
    (False, False, True,  False, False, True),    # 半成品/旧残留 -> 重跑 + warn
    (False, False, False, False, False, False),   # 未跑过 -> 正常重跑
])
def test_reconcile_decision_table(g, j, f, expected_completed, expected_aborted, expects_warning):
    state = reconcile(
        git_completed={"pre-recon"} if g else set(),
        session_completed={"pre-recon"} if j else set(),
        file_exists={"pre-recon": f},
        agent="pre-recon",
    )
    if expected_aborted:
        assert state.aborted is True
        assert state.abort_reason
        return
    assert state.aborted is False
    assert ("pre-recon" in state.completed_agents) is expected_completed
    assert bool(state.warnings) is expects_warning


def test_reconcile_abort_message_mentions_missing_file():
    state = reconcile(
        git_completed={"pre-recon"}, session_completed={"pre-recon"},
        file_exists={"pre-recon": False}, agent="pre-recon",
    )
    assert state.aborted
    assert "pre-recon" in state.abort_reason


import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from shannon_whitebox.pipeline.whitebox_resume import WhiteboxResumeStateBuilder


def _write_session(workspace: Path, agents_success: dict[str, bool]) -> None:
    data = {
        "repo_path": "/repo",
        "status": "running",
        "metrics": {
            "agents": {name: {"success": ok} for name, ok in agents_success.items()},
        },
    }
    (workspace / "session.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.asyncio
async def test_builder_auto_resume_skips_completed(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    deliverables = repo / ".shannon" / "deliverables"; deliverables.mkdir(parents=True)
    (deliverables / "pre_recon_deliverable.md").write_text("done")
    (deliverables / "recon_deliverable.md").write_text("done")
    workspace = tmp_path / "ws"; workspace.mkdir()
    _write_session(workspace, {"pre-recon": True, "recon": True})

    builder = WhiteboxResumeStateBuilder()
    with patch("shannon_whitebox.pipeline.whitebox_resume.GitManager.get_completed_agents",
               AsyncMock(return_value={"pre-recon", "recon"})):
        state = await builder.build(
            mode="auto", workspace=workspace, deliverables=deliverables, repo_path=repo,
        )

    assert state.completed_agents == ["pre-recon", "recon"]
    assert state.aborted is False
    assert state.interrupted_agent == "injection-vuln"  # 编排顺序里下一个


@pytest.mark.asyncio
async def test_builder_aborts_when_file_missing(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    deliverables = repo / ".shannon" / "deliverables"; deliverables.mkdir(parents=True)
    # 不写 recon_deliverable.md（文件缺失）
    workspace = tmp_path / "ws"; workspace.mkdir()
    _write_session(workspace, {"recon": True})

    builder = WhiteboxResumeStateBuilder()
    with patch("shannon_whitebox.pipeline.whitebox_resume.GitManager.get_completed_agents",
               AsyncMock(return_value={"recon"})):
        state = await builder.build(
            mode="auto", workspace=workspace, deliverables=deliverables, repo_path=repo,
        )

    assert state.aborted is True
    assert "recon" in (state.abort_reason or "")
