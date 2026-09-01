"""run_code_index activity: GitNexus 不可用必须硬失败(不降级 minimal)。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from temporalio.exceptions import ApplicationError as ApplicationFailure

from supernova_whitebox.pipeline.activities import run_code_index, _get_paths
from supernova_whitebox.pipeline.shared import ActivityInput
from supernova_core.utils.paths import WHITEBOX_SUBDIR


@pytest.mark.asyncio
async def test_run_code_index_raises_when_gitnexus_unavailable(tmp_path):
    """GitNexus CLI 不可用 → run_code_index 抛 ApplicationFailure,不再降级。"""
    input = ActivityInput(repo_path=str(tmp_path), workspace_name="test")

    with patch("supernova_whitebox.audit.session_registry.get_audit_session") as mock_sess, \
         patch("supernova_core.code_index.gitnexus_engine.GitNexusEngine") as mock_engine_cls, \
         patch("supernova_whitebox.pipeline.activities._get_paths") as mock_paths:
        # track_step 是 async context manager。
        # 关键:__aexit__ 必须返回 falsy,否则会吞掉块内抛出的异常
        # (真实 track_step 在 __aexit__ 里 re-raise,见 supernova_core.audit.session)。
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

    with patch("supernova_whitebox.audit.session_registry.get_audit_session") as mock_sess, \
         patch("supernova_core.code_index.gitnexus_engine.GitNexusEngine") as mock_engine_cls, \
         patch("supernova_whitebox.pipeline.activities._get_paths") as mock_paths, \
         patch("supernova_core.code_index.gitnexus_mcp.GitNexusMCPClient"), \
         patch("supernova_core.code_index.build_code_index_with_gitnexus",
               new=AsyncMock(return_value=(fake_index, [], [], []))), \
         patch("supernova_core.code_index.write_index_files",
               return_value=(tmp_path / "code_index.json", tmp_path / "code_index_summary.md")):
        cm = mock_sess.return_value.track_step.return_value
        cm.__aenter__ = AsyncMock(return_value=None)
        cm.__aexit__ = AsyncMock(return_value=None)
        # log_info 在 activity 中被 await，必须是 AsyncMock 才能记录 await_args。
        mock_sess.return_value.log_info = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = True
        mock_engine.ensure_indexed_async = AsyncMock(return_value=MagicMock(success=True))
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

    with patch("supernova_whitebox.audit.session_registry.get_audit_session") as mock_sess, \
         patch("supernova_core.code_index.gitnexus_engine.GitNexusEngine") as mock_engine_cls, \
         patch("supernova_whitebox.pipeline.activities._get_paths") as mock_paths, \
         patch("supernova_core.code_index.gitnexus_mcp.GitNexusMCPClient"), \
         patch("supernova_core.code_index.build_code_index_with_gitnexus",
               new=AsyncMock(return_value=(fake_index, [], [], []))), \
         patch("supernova_core.code_index.write_index_files",
               return_value=(tmp_path / "code_index.json", tmp_path / "code_index_summary.md")):
        cm = mock_sess.return_value.track_step.return_value
        cm.__aenter__ = AsyncMock(return_value=None)
        cm.__aexit__ = AsyncMock(return_value=None)
        # log_info 在 activity 中被 await，必须是 AsyncMock 才能记录 await_args。
        mock_sess.return_value.log_info = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = True
        mock_engine.ensure_indexed_async = AsyncMock(return_value=MagicMock(success=True))
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


def test_get_paths_prefers_workspace_path_for_web_scan(tmp_path):
    """web 扫描下 deliverables 必须落 ActivityInput.workspace_path（=scan_dir），
    而非 resolve_deliverables_path(workspace_name) 的平铺目录。

    根因（2026-07-30 端到端暴露）：WhiteboxScanWorkflow 已算 workspace_path=event_file.parent
    （web = scan_dir）传入 ActivityInput，但 _get_paths 无视它、改用 workspace_name 算
    workspaces/<scan_id>/deliverables，与 web DeliverablesReader 读的 scan_dir/deliverables
    分裂 → 前端 0 漏洞（产物真实存在 40+ 条却显示空）。_get_paths 须尊重 workspace_path。
    """
    # web scan_dir（<ws>/scans/<scan_id>）与 workspace_name=scan_id 的平铺落点是不同目录。
    scan_dir = tmp_path / "workspaces" / "__legacy__" / "scans" / "NodeGoat-x"
    scan_dir.mkdir(parents=True)
    inp = ActivityInput(
        repo_path=str(tmp_path / "repos" / "NodeGoat"),
        workspace_name="NodeGoat-x",          # = scan_id（_get_paths 旧平铺落点）
        workspace_path=str(scan_dir),          # web: event_file.parent
        deliverables_subdir="deliverables",
    )

    _, deliverables, _ = _get_paths(inp)

    assert deliverables == scan_dir / "deliverables" / WHITEBOX_SUBDIR





@pytest.mark.asyncio
async def test_run_code_index_mcp_timeout_is_retryable(tmp_path):
    """MCP 握手/连接超时(ConnectionError) → retryable ApplicationFailure(temporal 重试)。

    真机 NodeGoat-20260821-044404：LLM subagent 并发抢 CPU，gitnexus mcp 冷启动
    >30s，initialize 读超时(30s) -> ConnectionError -> PentestError 默认
    retryable=False -> non-retryable -> 整扫 fail-fast。连接类瞬时错误可重试
    （重试时 node 二进制已进 page cache，二次启动快），CODE_INDEX_RETRY(max 3)
    兜底，仍失败才真死；engine 不可用/索引失败等配置类错误保持 non-retryable。"""
    input = ActivityInput(repo_path=str(tmp_path), workspace_name="test")

    mcp_client = MagicMock()
    mcp_client.__aenter__ = AsyncMock(
        side_effect=ConnectionError("GitNexus MCP timed out after 30s waiting for response"))
    mcp_client.__aexit__ = AsyncMock(return_value=False)

    with patch("supernova_whitebox.audit.session_registry.get_audit_session") as mock_sess, \
         patch("supernova_core.code_index.gitnexus_engine.GitNexusEngine") as mock_engine_cls, \
         patch("supernova_whitebox.pipeline.activities._get_paths") as mock_paths, \
         patch("supernova_core.code_index.gitnexus_mcp.GitNexusMCPClient",
               return_value=mcp_client):
        cm = mock_sess.return_value.track_step.return_value
        cm.__aenter__ = AsyncMock(return_value=None)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = True
        mock_engine.ensure_indexed_async = AsyncMock(return_value=MagicMock(success=True))
        mock_engine_cls.return_value = mock_engine
        mock_paths.return_value = (tmp_path, tmp_path / "deliverables", tmp_path)

        with pytest.raises(ApplicationFailure, match="GitNexus MCP") as ei:
            await run_code_index(input)
        # 关键断言：连接类瞬时错误必须可重试（non_retryable=False），
        # 让 CODE_INDEX_RETRY 接管而非把整条扫描打死。
        assert ei.value.non_retryable is False


@pytest.mark.asyncio
async def test_run_code_index_passes_discovery_agent_when_enabled(tmp_path):
    """spec 2026-08-27 §5：GitNexus-LLM 开 → discovery 走多轮 agent（非 None
    discovery_agent 传入 build_code_index_with_gitnexus）；关 → None（单次降级）。"""
    from supernova_core.config import concurrency as conc

    input = ActivityInput(repo_path=str(tmp_path), workspace_name="test")
    fake_index = MagicMock(
        total_blocks=1, total_entry_points=0, total_chains=0, degradation_level="full",
    )
    for enabled, expect_agent in ((True, True), (False, False)):
        with patch("supernova_whitebox.audit.session_registry.get_audit_session") as mock_sess, \
             patch("supernova_core.code_index.gitnexus_engine.GitNexusEngine") as mock_engine_cls, \
             patch("supernova_whitebox.pipeline.activities._get_paths") as mock_paths, \
             patch("supernova_core.code_index.gitnexus_mcp.GitNexusMCPClient"), \
             patch("supernova_core.code_index.build_code_index_with_gitnexus",
                   new=AsyncMock(return_value=(fake_index, [], [], []))) as mock_build, \
             patch("supernova_core.code_index.write_index_files",
                   return_value=(tmp_path / "code_index.json", tmp_path / "code_index_summary.md")), \
             patch("supernova_whitebox.pipeline.activities.is_gitnexus_llm_enabled",
                   lambda: enabled):
            cm = mock_sess.return_value.track_step.return_value
            cm.__aenter__ = AsyncMock(return_value=None)
            cm.__aexit__ = AsyncMock(return_value=None)
            mock_engine = MagicMock()
            mock_engine.is_available.return_value = True
            mock_engine.ensure_indexed_async = AsyncMock(
                return_value=MagicMock(success=True))
            mock_engine_cls.return_value = mock_engine
            mock_paths.return_value = (tmp_path, tmp_path / "deliverables", tmp_path)

            await run_code_index(input)

            kwargs = mock_build.call_args.kwargs
            has_agent = kwargs.get("discovery_agent") is not None
            assert has_agent is expect_agent, f"enabled={enabled}"
