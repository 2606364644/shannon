"""P3c 阶段 1：白盒 activity 把 input.provider_config 下传 run_claude_prompt。

只验穿线（mock run_claude_prompt），不跑真实 agent / temporalio 上下文。
覆盖 2 个模块级纯函数调用点（_make_recon_summary_llm_client /
run_gitnexus_verdict_agent；chain-verdict 单次工厂已拆，2026-09-01）。

不在直测范围（按 plan 说明靠回归 + Task 5 e2e 覆盖）：
- _make_gitnexus_llm_client :672（嵌套在 run_code_index 局部函数）
- executor.execute :208 / build_provider_config :641/:734（@activity.defn 内，
  依赖 activity.info()+session 上下文）
"""
import pytest

from supernova_core.agents.runner import ClaudeRunResult
from supernova_whitebox.pipeline import activities


async def test_make_recon_summary_llm_client_passes_provider_config(monkeypatch):
    """_make_recon_summary_llm_client 收 provider_config → 闭包内 run_claude_prompt 收到。"""
    captured = {}

    async def fake_run(**kw):
        captured.update(kw)
        return ClaudeRunResult(success=True)

    monkeypatch.setattr(activities, "run_claude_prompt", fake_run)
    client = activities._make_recon_summary_llm_client(
        "/r", provider_config={"type": "openai_compatible", "api_key": "sk"}
    )
    await client("prompt")
    assert captured["provider_config"] == {"type": "openai_compatible", "api_key": "sk"}


async def test_run_gitnexus_verdict_agent_passes_provider_config(monkeypatch):
    """run_gitnexus_verdict_agent 收 provider_config → run_claude_prompt 收到。

    verdict_agent 延迟 import run_claude_prompt，patch 源模块（对齐现有
    test_gitnexus_verdict_agent.py 模式）。
    """
    captured = {}

    async def fake_run(**kw):
        captured.update(kw)
        return ClaudeRunResult(success=True)

    monkeypatch.setattr("supernova_core.agents.runner.run_claude_prompt", fake_run)
    pc = {"type": "openai_compatible", "api_key": "sk-v"}
    await activities.run_gitnexus_verdict_agent(prompt="p", repo_path="/r", provider_config=pc)
    assert captured["provider_config"] == pc
