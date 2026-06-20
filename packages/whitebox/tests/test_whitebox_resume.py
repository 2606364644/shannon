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
    workspace = tmp_path / "ws"; workspace.mkdir()
    deliverables = workspace / "deliverables"; deliverables.mkdir(parents=True)
    (deliverables / "pre_recon_deliverable.md").write_text("done")
    (deliverables / "recon_deliverable.md").write_text("done")
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
    workspace = tmp_path / "ws"; workspace.mkdir()
    deliverables = workspace / "deliverables"; deliverables.mkdir(parents=True)
    # 不写 recon_deliverable.md（文件缺失）
    _write_session(workspace, {"recon": True})

    builder = WhiteboxResumeStateBuilder()
    with patch("shannon_whitebox.pipeline.whitebox_resume.GitManager.get_completed_agents",
               AsyncMock(return_value={"recon"})):
        state = await builder.build(
            mode="auto", workspace=workspace, deliverables=deliverables, repo_path=repo,
        )

    assert state.aborted is True
    assert "recon" in (state.abort_reason or "")


@pytest.mark.asyncio
async def test_cleanup_auto_deletes_partial_deliverable(tmp_path):
    deliverables = tmp_path / "deliverables"; deliverables.mkdir()
    (deliverables / "recon_deliverable.md").write_text("half-baked")  # ¬G 半成品

    builder = WhiteboxResumeStateBuilder()
    await builder.cleanup(
        mode="auto", deliverables=deliverables,
        completed_agents=[],
    )

    assert not (deliverables / "recon_deliverable.md").exists()


@pytest.mark.asyncio
async def test_cleanup_rewind_archives_target_and_after(tmp_path):
    deliverables = tmp_path / "deliverables"; deliverables.mkdir()
    (deliverables / "pre_recon_deliverable.md").write_text("keep")
    (deliverables / "recon_deliverable.md").write_text("archive")  # rewind 目标
    (deliverables / "injection_analysis_deliverable.md").write_text("archive")  # 之后

    builder = WhiteboxResumeStateBuilder()
    archived = await builder.cleanup(
        mode="rewind", deliverables=deliverables,
        completed_agents=["pre-recon"],
        rewind_target="recon", run_ts="20260619-1530",
    )

    assert (deliverables / "pre_recon_deliverable.md").exists()  # 之前保留
    archive_dir = deliverables / ".whitebox-archive" / "20260619-1530"
    assert (archive_dir / "recon_deliverable.md").exists()
    assert (archive_dir / "injection_analysis_deliverable.md").exists()
    assert not (deliverables / "recon_deliverable.md").exists()


@pytest.mark.asyncio
async def test_builder_rewind_keeps_only_before_target(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    workspace = tmp_path / "ws"; workspace.mkdir()
    deliverables = workspace / "deliverables"; deliverables.mkdir(parents=True)
    for f in ("pre_recon_deliverable.md", "recon_deliverable.md", "injection_analysis_deliverable.md"):
        (deliverables / f).write_text("done")
    _write_session(workspace, {"pre-recon": True, "recon": True, "injection-vuln": True})

    builder = WhiteboxResumeStateBuilder()
    with patch("shannon_whitebox.pipeline.whitebox_resume.GitManager.get_completed_agents",
               AsyncMock(return_value={"pre-recon", "recon", "injection-vuln"})):
        state = await builder.build(
            mode="rewind", workspace=workspace, deliverables=deliverables,
            repo_path=repo, rewind_target="recon",
        )

    assert state.completed_agents == ["pre-recon"]  # 只保留 recon 之前
    assert state.interrupted_agent == "recon"


@pytest.mark.asyncio
async def test_builder_rewind_vuln_maps_to_injection_vuln(tmp_path):
    """I-1: --rewind vuln 规范化到 injection-vuln（vuln 段起点）。

    - completed_agents 只保留 injection-vuln 之前的（pre-recon / recon）
    - interrupted_agent == "injection-vuln"
    - cleanup(mode="rewind", rewind_target="vuln") 不 crash，归档 injection-vuln 及之后
    """
    repo = tmp_path / "repo"; repo.mkdir()
    workspace = tmp_path / "ws"; workspace.mkdir()
    deliverables = workspace / "deliverables"; deliverables.mkdir(parents=True)
    for f in ("pre_recon_deliverable.md", "recon_deliverable.md",
              "injection_analysis_deliverable.md", "xss_analysis_deliverable.md"):
        (deliverables / f).write_text("done")
    _write_session(workspace, {
        "pre-recon": True, "recon": True,
        "injection-vuln": True, "xss-vuln": True,
    })

    builder = WhiteboxResumeStateBuilder()
    with patch("shannon_whitebox.pipeline.whitebox_resume.GitManager.get_completed_agents",
               AsyncMock(return_value={"pre-recon", "recon", "injection-vuln", "xss-vuln"})):
        state = await builder.build(
            mode="rewind", workspace=workspace, deliverables=deliverables,
            repo_path=repo, rewind_target="vuln",  # 别名
        )

    # vuln 别名 -> injection-vuln：只保留它之前的 pre-recon / recon
    assert state.completed_agents == ["pre-recon", "recon"]
    assert state.interrupted_agent == "injection-vuln"

    # cleanup 用原始别名 "vuln" 也不应 crash（内部规范化）
    archived = await builder.cleanup(
        mode="rewind", deliverables=deliverables,
        completed_agents=state.completed_agents,
        rewind_target="vuln", run_ts="20260619-1600",
    )
    archive_dir = deliverables / ".whitebox-archive" / "20260619-1600"
    assert archived == archive_dir
    # injection-vuln 及之后被归档；pre-recon/recon 保留
    assert (archive_dir / "injection_analysis_deliverable.md").exists()
    assert (archive_dir / "xss_analysis_deliverable.md").exists()
    assert (deliverables / "pre_recon_deliverable.md").exists()
    assert (deliverables / "recon_deliverable.md").exists()
    assert not (deliverables / "injection_analysis_deliverable.md").exists()


def test_session_success_swallows_corrupt_json(tmp_path):
    """M-2: session.json 损坏（JSONDecodeError）不应抛到 worker，返回空集。"""
    workspace = tmp_path / "ws"; workspace.mkdir()
    (workspace / "session.json").write_text("{ not valid json", encoding="utf-8")
    builder = WhiteboxResumeStateBuilder()
    assert builder._session_success(workspace) == set()
