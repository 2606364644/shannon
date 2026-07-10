"""run_code_index activity: GitNexus 不可用必须硬失败(不降级 minimal)。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from temporalio.exceptions import ApplicationError as ApplicationFailure

from shannon_whitebox.pipeline.activities import run_code_index, _get_paths
from shannon_whitebox.pipeline.shared import ActivityInput


@pytest.mark.asyncio
async def test_run_code_index_raises_when_gitnexus_unavailable(tmp_path):
    """GitNexus CLI 不可用 → run_code_index 抛 ApplicationFailure,不再降级。"""
    input = ActivityInput(repo_path=str(tmp_path), workspace_name="test")

    with patch("shannon_whitebox.audit.session_registry.get_audit_session") as mock_sess, \
         patch("shannon_core.code_index.gitnexus_engine.GitNexusEngine") as mock_engine_cls, \
         patch("shannon_whitebox.pipeline.activities._get_paths") as mock_paths:
        # track_step 是 async context manager。
        # 关键:__aexit__ 必须返回 falsy,否则会吞掉块内抛出的异常
        # (真实 track_step 在 __aexit__ 里 re-raise,见 shannon_core.audit.session)。
        cm = mock_sess.return_value.track_step.return_value
        cm.__aenter__ = AsyncMock(return_value=None)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = False
        mock_engine_cls.return_value = mock_engine
        mock_paths.return_value = (tmp_path, tmp_path / "deliverables", tmp_path)

        with pytest.raises(ApplicationFailure, match="GitNexus"):
            await run_code_index(input)


@pytest.mark.asyncio
async def test_run_code_index_logs_chains_warning_when_empty(tmp_path):
    """chains=0 时 log_info 发 warning（调用图空壳 → GitNexus 轨无结果的核心信号）。
    对齐 06-29 authz/injection-gitnexus-track-observability 的 InfoEvent 模式。"""
    input = ActivityInput(repo_path=str(tmp_path), workspace_name="test")
    fake_index = MagicMock(
        total_blocks=10, total_entry_points=0, total_chains=0, degradation_level="full",
    )

    with patch("shannon_whitebox.audit.session_registry.get_audit_session") as mock_sess, \
         patch("shannon_core.code_index.gitnexus_engine.GitNexusEngine") as mock_engine_cls, \
         patch("shannon_whitebox.pipeline.activities._get_paths") as mock_paths, \
         patch("shannon_core.code_index.gitnexus_mcp.GitNexusMCPClient"), \
         patch("shannon_core.code_index.build_code_index_with_gitnexus",
               new=AsyncMock(return_value=(fake_index, [], []))), \
         patch("shannon_core.code_index.write_index_files",
               return_value=(tmp_path / "code_index.json", tmp_path / "code_index_summary.md")):
        cm = mock_sess.return_value.track_step.return_value
        cm.__aenter__ = AsyncMock(return_value=None)
        cm.__aexit__ = AsyncMock(return_value=None)
        # log_info 在 activity 中被 await，必须是 AsyncMock 才能记录 await_args。
        mock_sess.return_value.log_info = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = True
        mock_engine.ensure_indexed.return_value = MagicMock(success=True)
        mock_engine_cls.return_value = mock_engine
        mock_paths.return_value = (tmp_path, tmp_path / "deliverables", tmp_path)

        await run_code_index(input)

        mock_sess.return_value.log_info.assert_awaited()
        args = mock_sess.return_value.log_info.await_args
        assert args.args[1] == "warning"
        assert "chains=0" in args.args[0]


@pytest.mark.asyncio
async def test_run_code_index_logs_info_when_chains_present(tmp_path):
    """chains>0 时 log_info 发 info（调用图正常）。"""
    input = ActivityInput(repo_path=str(tmp_path), workspace_name="test")
    fake_index = MagicMock(
        total_blocks=10, total_entry_points=3, total_chains=5, degradation_level="none",
    )

    with patch("shannon_whitebox.audit.session_registry.get_audit_session") as mock_sess, \
         patch("shannon_core.code_index.gitnexus_engine.GitNexusEngine") as mock_engine_cls, \
         patch("shannon_whitebox.pipeline.activities._get_paths") as mock_paths, \
         patch("shannon_core.code_index.gitnexus_mcp.GitNexusMCPClient"), \
         patch("shannon_core.code_index.build_code_index_with_gitnexus",
               new=AsyncMock(return_value=(fake_index, [], []))), \
         patch("shannon_core.code_index.write_index_files",
               return_value=(tmp_path / "code_index.json", tmp_path / "code_index_summary.md")):
        cm = mock_sess.return_value.track_step.return_value
        cm.__aenter__ = AsyncMock(return_value=None)
        cm.__aexit__ = AsyncMock(return_value=None)
        # log_info 在 activity 中被 await，必须是 AsyncMock 才能记录 await_args。
        mock_sess.return_value.log_info = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = True
        mock_engine.ensure_indexed.return_value = MagicMock(success=True)
        mock_engine_cls.return_value = mock_engine
        mock_paths.return_value = (tmp_path, tmp_path / "deliverables", tmp_path)

        await run_code_index(input)

        args = mock_sess.return_value.log_info.await_args
        assert args.args[1] == "info"
        assert "chains=5" in args.args[0]


def test_get_paths_routes_deliverables_to_whitebox_subdir(tmp_path):
    """_get_paths 必须把 deliverables 落到 whitebox/ 子目录（Task 2 产物隔离核心改动）。

    不 monkeypatch _get_paths，直接调用真实实现，守护
    `deliverables = deliverables / WHITEBOX_SUBDIR` 这一行——其他 whitebox 测试都
    patch 掉 _get_paths，该行因此无执行覆盖（回归风险缺口）。本测试补这块覆盖：
    只给 repo_path（不给 workspace_name），resolve_deliverables_path 走过渡兼容分支
    返回 Path(repo_path)/deliverables，再经 _get_paths 追加 WHITEBOX_SUBDIR。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    inp = ActivityInput(repo_path=str(repo))

    _, deliverables, _ = _get_paths(inp)

    assert deliverables.name == "whitebox"
    assert deliverables.parent.name == "deliverables"

