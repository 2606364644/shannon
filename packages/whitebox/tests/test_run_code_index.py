"""run_code_index activity: GitNexus 不可用必须硬失败(不降级 minimal)。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from temporalio.exceptions import ApplicationError as ApplicationFailure

from supernova_whitebox.pipeline.activities import run_code_index, _get_paths
from supernova_whitebox.pipeline.shared import ActivityInput


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


@pytest.mark.asyncio
async def test_gitnexus_llm_client_passes_output_format_and_prefers_structured_output(tmp_path):
    """路径 A 核心：_make_verdict_llm_client 的 _client（与 _make_gitnexus_llm_client 的
    嵌套 _client 同构）必须把调用方传入的 output_format 透传给 run_claude_prompt
    （structured_output_schema=），并优先返回 structured_output（json.dumps 还原 str 契约），
    而非裸 result.text。

    根因：旧实现不传 output_format + 读 result.text，GLM 返回 Markdown 文本致下游
    json.loads 崩（taint-analysis WARNING 刷屏）。经 CLI --json-schema 强制合法 JSON 后，
    SDK 原生 structured_output 优先；缺失时 provider _extract_json_payload 兜底。
    _make_gitnexus_llm_client 是 run_code_index 内嵌套函数无法独立访问，其 _client 逻辑
    与模块级 _make_verdict_llm_client 完全一致，由后者单测守护。
    """
    import json
    from unittest.mock import patch
    from supernova_whitebox.pipeline import activities
    from supernova_core.agents.runner import ClaudeRunResult

    captured = {}

    async def fake_run_claude_prompt(**kwargs):
        captured["kwargs"] = kwargs
        return ClaudeRunResult(
            text="# 判定(被忽略, structured_output 优先)\n```json\n{}```",
            success=True,
            structured_output={"verdict": "vulnerable", "evidence_chain": "ip->sink"},
        )

    with patch.object(activities, "is_gitnexus_llm_enabled", return_value=True), \
         patch("supernova_core.agents.runner.run_claude_prompt", side_effect=fake_run_claude_prompt):
        client = activities._make_verdict_llm_client(str(tmp_path))
        schema = {"type": "object", "properties": {"verdict": {"type": "string"}}}
        out = await client("judge this", output_format=schema)

    # 1. output_format 经 structured_output_schema 透传给 run_claude_prompt
    assert captured["kwargs"].get("structured_output_schema") is schema, \
        "output_format 必须透传为 structured_output_schema"
    # 2. 优先返回 structured_output（json.dumps 成 str 契约），非 .text
    assert json.loads(out) == {"verdict": "vulnerable", "evidence_chain": "ip->sink"}, \
        "应返回 structured_output 而非 result.text"


@pytest.mark.asyncio
async def test_gitnexus_llm_client_falls_back_to_text_when_no_structured_output(tmp_path):
    """structured_output 为 None 时（SDK 原生 + provider extract 都没拿到合法 JSON），
    回退 result.text，让下游 _extract_json_payload + deterministic fallback 兜底（三重防线）。"""
    from unittest.mock import patch
    from supernova_whitebox.pipeline import activities
    from supernova_core.agents.runner import ClaudeRunResult

    async def fake_run_claude_prompt(**kwargs):
        return ClaudeRunResult(text="not even json", success=True, structured_output=None)

    with patch.object(activities, "is_gitnexus_llm_enabled", return_value=True), \
         patch("supernova_core.agents.runner.run_claude_prompt", side_effect=fake_run_claude_prompt):
        client = activities._make_verdict_llm_client(str(tmp_path))
        out = await client("analyze", output_format={"type": "object"})

    assert out == "not even json", "structured_output 为 None 时应回退 result.text"



