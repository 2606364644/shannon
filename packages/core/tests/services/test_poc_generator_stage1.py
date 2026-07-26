"""P3c 阶段 1：poc_generator 把 provider_config 下传 run_claude_prompt。

只验穿线（mock run_claude_prompt，空 partials 免造 PartialSpec）。
poc_generator 的 run_claude_prompt 是模块级 import（便于 monkeypatch）。
"""
import pytest

from supernova_core.agents.runner import ClaudeRunResult


async def test_llm_fill_gaps_passes_provider_config(monkeypatch):
    """llm_fill_gaps 收 provider_config → run_claude_prompt 收到同一 dict。"""
    captured = {}

    async def fake_run(prompt, repo_path, **kw):
        captured["provider_config"] = kw.get("provider_config")
        return ClaudeRunResult(success=True, structured_output={"items": []})

    monkeypatch.setattr("supernova_core.services.poc_generator.run_claude_prompt", fake_run)
    from supernova_core.services.poc_generator import llm_fill_gaps

    pc = {"type": "openai_compatible", "api_key": "sk-poc"}
    await llm_fill_gaps(None, [], recon_ctx={}, repo_path="/tmp", provider_config=pc)
    assert captured["provider_config"] == pc


async def test_llm_fill_gaps_provider_config_default_none(monkeypatch):
    """不传 provider_config → run_claude_prompt 收 None（CLI 兜底 env，行为不变）。"""
    captured = {}

    async def fake_run(prompt, repo_path, **kw):
        captured["provider_config"] = kw.get("provider_config")
        return ClaudeRunResult(success=True, structured_output={"items": []})

    monkeypatch.setattr("supernova_core.services.poc_generator.run_claude_prompt", fake_run)
    from supernova_core.services.poc_generator import llm_fill_gaps

    await llm_fill_gaps(None, [], recon_ctx={}, repo_path="/tmp")
    assert captured["provider_config"] is None
