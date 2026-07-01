import pytest
from unittest.mock import AsyncMock, MagicMock
from shannon_whitebox.pipeline import activities


@pytest.mark.asyncio
async def test_verdict_agent_reads_max_turns_env(monkeypatch):
    """SHANNON_GITNEXUS_VERDICT_MAX_TURNS env 透传给 run_claude_prompt。"""
    monkeypatch.setenv("SHANNON_GITNEXUS_VERDICT_MAX_TURNS", "7")
    captured: dict = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        result = MagicMock()
        result.text = "ok"
        result.success = True
        result.turns = 1
        return result

    # 延迟 import 从源模块取，patch 源模块有效
    monkeypatch.setattr("shannon_core.agents.runner.run_claude_prompt", fake_run)

    await activities.run_gitnexus_verdict_agent(prompt="p", repo_path="/r")

    assert captured["max_turns"] == 7
    assert captured["model_tier"] == "medium"


@pytest.mark.asyncio
async def test_verdict_agent_default_max_turns(monkeypatch):
    """不设 env 时默认 30。"""
    monkeypatch.delenv("SHANNON_GITNEXUS_VERDICT_MAX_TURNS", raising=False)
    captured: dict = {}
    async def fake_run(**kwargs):
        captured.update(kwargs)
        return MagicMock(text="ok", success=True, turns=1)
    monkeypatch.setattr("shannon_core.agents.runner.run_claude_prompt", fake_run)

    await activities.run_gitnexus_verdict_agent(prompt="p", repo_path="/r")
    assert captured["max_turns"] == 30
