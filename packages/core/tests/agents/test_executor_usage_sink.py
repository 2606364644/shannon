"""executor usage_sink：cancel 中途的已花 usage 出口（2026-08-28 authcheck 超时丢账修复）。

背景：Temporal start_to_close_timeout 到期以 CancelledError cancel 掉 activity，
except Exception 接不住（BaseException 分支）→ run_claude_prompt 正常返回的
usage 拿不到 → AgentEvent end 缺失、记账 0。修复通道：executor.execute 每次创建
UsageSink 挂 self.usage_sink 并传给 run_claude_prompt；provider 在被 cancel 前把
已累积 usage 写 sink；activity 的 cancel 兜底从 executor.usage_sink 读已花值记账。

本文件只验 executor 职责（创建/挂载/传递/异常传播）；provider 侧写入语义见
test_providers_openai_usage_sink.py。
"""
import asyncio

import pytest

from supernova_core.agents.executor import AgentExecutor
from supernova_core.agents.runner import ClaudeRunResult
from supernova_core.models.agents import AgentName


def _stub_prompt_manager():
    return type("PM", (), {"load_sync": lambda self, *a, **k: "prompt"})()


@pytest.fixture
def shell(monkeypatch):
    """屏蔽 execute 成功路径副作用（GitManager / spending-cap），返回捕获盒。"""
    box = {}

    async def fake_run(prompt, repo_path, **kw):
        box["usage_sink"] = kw.get("usage_sink")
        return ClaudeRunResult(success=True, structured_output=None)

    monkeypatch.setattr("supernova_core.agents.executor.run_claude_prompt", fake_run)

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr("supernova_core.agents.executor.GitManager.ensure_repository", _noop)
    monkeypatch.setattr("supernova_core.agents.executor.GitManager.create_checkpoint", _noop)
    monkeypatch.setattr("supernova_core.agents.executor.GitManager.commit", _noop)
    monkeypatch.setattr(
        "supernova_core.agents.executor.is_spending_cap_behavior",
        lambda *a, **k: False)
    return box


async def test_execute_creates_and_passes_usage_sink(shell, tmp_path):
    """execute 创建 UsageSink 挂实例并传给 run_claude_prompt（同一对象）。"""
    exe = AgentExecutor(prompt_manager=_stub_prompt_manager())
    await exe.execute(
        agent_name=AgentName.RECON,
        repo_path=str(tmp_path),
        deliverables_path=str(tmp_path / "deliv"),
        skip_artifact_postprocess=True,
    )
    assert exe.usage_sink is not None
    assert shell["usage_sink"] is exe.usage_sink


async def test_execute_cancel_propagates_and_sink_keeps_partial(shell, tmp_path):
    """run_claude_prompt 被 cancel：CancelledError 原样传播，sink 保留 provider 已写的部分 usage。

    provider cancel 分支先写 sink 再 re-raise（本测试用 fake 模拟该行为）——
    activity 兜底靠 executor.usage_sink 在异常后仍可读。
    """
    exe = AgentExecutor(prompt_manager=_stub_prompt_manager())

    async def cancelled_run(prompt, repo_path, **kw):
        sink = kw.get("usage_sink")
        sink.record(
            model="deepseek-v4-flash",
            input_tokens=1200, output_tokens=300,
            cache_read_tokens=8000, cache_creation_tokens=0,
            cost_usd=0.0046, cost_currency="CNY",
        )
        raise asyncio.CancelledError()

    import supernova_core.agents.executor as executor_mod
    monkeypatch_local = pytest.MonkeyPatch()
    monkeypatch_local.setattr(executor_mod, "run_claude_prompt", cancelled_run)
    try:
        with pytest.raises(asyncio.CancelledError):
            await exe.execute(
                agent_name=AgentName.VALIDATE_AUTH,
                repo_path=str(tmp_path),
                deliverables_path=str(tmp_path / "deliv"),
                skip_artifact_postprocess=True,
            )
    finally:
        monkeypatch_local.undo()
    # cancel 后 sink 仍挂在 executor 上，值 = provider 写入的部分消耗
    assert exe.usage_sink.input_tokens == 1200
    assert exe.usage_sink.output_tokens == 300
    assert exe.usage_sink.cache_read_tokens == 8000
    assert exe.usage_sink.cost_usd == pytest.approx(0.0046)
    assert exe.usage_sink.cost_currency == "CNY"
    assert exe.usage_sink.model == "deepseek-v4-flash"


async def test_usage_sink_resets_between_executes(shell, tmp_path):
    """连续两次 execute：sink 不跨次串账（第二次是全新实例、字段归零）。"""
    exe = AgentExecutor(prompt_manager=_stub_prompt_manager())
    first_sink = exe.usage_sink  # 尚未 execute → None（未创建）
    assert first_sink is None
    await exe.execute(
        agent_name=AgentName.RECON,
        repo_path=str(tmp_path),
        deliverables_path=str(tmp_path / "deliv"),
        skip_artifact_postprocess=True,
    )
    sink_after_first = exe.usage_sink
    await exe.execute(
        agent_name=AgentName.RECON,
        repo_path=str(tmp_path),
        deliverables_path=str(tmp_path / "deliv"),
        skip_artifact_postprocess=True,
    )
    assert exe.usage_sink is not sink_after_first
