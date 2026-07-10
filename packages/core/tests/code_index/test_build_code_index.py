"""build_code_index_with_gitnexus 的 progress_cb 透传测试（T5）。

本文件只覆盖 progress_cb 通道本身（None / callable 两条路径不爆），
discover_sinks_llm / discover_sources_llm 的详细 sample 断言由 T4 单测层覆盖，
analyze_taint_llm 的返回结构由 test_llm_taint_analyzer.py 覆盖。
"""
import asyncio
import os
import tempfile

import pytest
from unittest.mock import AsyncMock, patch

from shannon_core.code_index import build_code_index_with_gitnexus
from shannon_core.code_index.progress import ProgressSample


def _make_repo(src: str) -> str:
    """tmp repo with a single app.js + .git dir（被 detect_language 当 repo）。"""
    repo = tempfile.mkdtemp()
    with open(os.path.join(repo, "app.js"), "w") as fh:
        fh.write(src)
    os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
    return repo


def _fake_mcp():
    mcp = AsyncMock()
    mcp.call_tool = AsyncMock(return_value={"upstream": [], "downstream": []})
    return mcp


# handler 内带一个 sink（eval），使 taint-analysis 环节真有 item tick
_SRC_WITH_SINK = (
    "function setupRoutes(app) {\n"
    "  app.get('/exec/:cmd', function runCmd(req, res){\n"
    "    const cmd = req.params.cmd;\n"
    "    eval(cmd);\n"
    "  });\n"
    "}\n"
)


@pytest.mark.asyncio
async def test_build_code_index_progress_cb_none_path_no_crash():
    """progress_cb=None 全程 no-op，不爆（默认参数路径）。"""
    repo = _make_repo(_SRC_WITH_SINK)
    fake_llm = AsyncMock(return_value="[]")
    index, _rule_gaps, _source_gaps = await build_code_index_with_gitnexus(
        repo, mcp_client=_fake_mcp(), llm_client=fake_llm,
        auto_index=False, progress_cb=None,
    )
    assert index.language == "typescript"


@pytest.mark.asyncio
async def test_build_code_index_threads_progress_cb_callable():
    """progress_cb=<callable>：sink/source/taint 三环节都应至少发 finalize sample。

    LLM 返回 "[]" → discover_* 无产出（仍 finalize），analyze_taint_llm 走 fallback
    （IntraResult.hits 可能为空 → taint tick hits_delta=0，但 finalize 一定发）。
    断言：samples 非空、final 行存在、phase 集合至少含三环节之一。
    """
    samples: list[ProgressSample] = []

    async def cb(s):
        samples.append(s)

    repo = _make_repo(_SRC_WITH_SINK)
    fake_llm = AsyncMock(return_value="[]")
    await build_code_index_with_gitnexus(
        repo, mcp_client=_fake_mcp(), llm_client=fake_llm,
        auto_index=False, progress_cb=cb,
    )

    assert len(samples) > 0, "progress_cb 未收到任何 sample"
    phases = {s.phase for s in samples}
    # 三环节至少各发一次（即便 0 命中，finalize 也会发）
    assert "sink-discovery" in phases, f"缺 sink-discovery sample, got {phases}"
    assert "source-discovery" in phases, f"缺 source-discovery sample, got {phases}"
    assert "taint-analysis" in phases, f"缺 taint-analysis sample, got {phases}"
    # 至少有一个 final 汇总行
    assert any(s.final for s in samples), "无 final 汇总 sample"


@pytest.mark.asyncio
async def test_build_code_index_offloads_parse_to_thread():
    """tree-sitter 解析+检测段经 _parse_and_detect_sync（由 asyncio.to_thread 调用）移出
    event loop（cancel 可注入）。治本：原本同步全量解析占死 worker loop → Ctrl+C 不可达。"""
    repo = _make_repo(_SRC_WITH_SINK)
    fake_llm = AsyncMock(return_value="[]")
    with patch("shannon_core.code_index._parse_and_detect_sync") as mock_parse:
        mock_parse.return_value = ({}, [], [], [])  # (file_sources, all_blocks, sinks, suspicious)
        await build_code_index_with_gitnexus(
            repo, mcp_client=_fake_mcp(), llm_client=fake_llm,
            auto_index=False, progress_cb=None,
        )
    mock_parse.assert_called_once()  # 解析段确实经 to_thread 调用了 helper


@pytest.mark.asyncio
async def test_build_code_index_passes_model_to_discovery(monkeypatch):
    """build_code_index_with_gitnexus 的 model 参数透传到 discover_sinks/sources_llm(spec §3 模块3)。"""
    import shannon_core.code_index as ci

    captured = {}

    async def fake_discover_sinks(suspicious, llm_client, *, model=None, **kw):
        captured["sinks_model"] = model
        return [], []

    async def fake_discover_sources(candidates, llm_client, *, model=None, **kw):
        captured["sources_model"] = model
        return [], []

    monkeypatch.setattr(ci, "discover_sinks_llm", fake_discover_sinks)
    monkeypatch.setattr(ci, "discover_sources_llm", fake_discover_sources)

    repo = _make_repo(_SRC_WITH_SINK)
    fake_llm = AsyncMock(return_value="[]")
    await build_code_index_with_gitnexus(
        repo, mcp_client=_fake_mcp(), llm_client=fake_llm,
        auto_index=False, progress_cb=None, model="glm-5.2",
    )
    assert captured.get("sinks_model") == "glm-5.2"
    assert captured.get("sources_model") == "glm-5.2"


@pytest.mark.asyncio
async def test_build_code_index_model_defaults_none(monkeypatch):
    """不传 model -> 默认 None 透传(discovery 走默认 context, 不阻断)。"""
    import shannon_core.code_index as ci

    captured = {}

    async def fake_discover_sinks(suspicious, llm_client, *, model=None, **kw):
        captured["sinks_model"] = model
        return [], []

    monkeypatch.setattr(ci, "discover_sinks_llm", fake_discover_sinks)
    monkeypatch.setattr(ci, "discover_sources_llm", fake_discover_sinks)

    repo = _make_repo(_SRC_WITH_SINK)
    fake_llm = AsyncMock(return_value="[]")
    await build_code_index_with_gitnexus(
        repo, mcp_client=_fake_mcp(), llm_client=fake_llm,
        auto_index=False, progress_cb=None,  # 不传 model
    )
    assert captured.get("sinks_model") is None
