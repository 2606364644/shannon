"""P3c 阶段 1：AgentExecutor.execute 把 provider_config 下传 run_claude_prompt。

只验穿线（mock run_claude_prompt + GitManager 静态方法 + spending-cap），不跑真模型。
None=CLI 兜底 env（行为不变）；非 None=web 穿线值原样到 run_claude_prompt。
"""
import pytest

from supernova_core.agents.executor import AgentExecutor
from supernova_core.agents.runner import ClaudeRunResult
from supernova_core.models.agents import AgentName


def _stub_prompt_manager():
    """execute() 仅调 prompt_manager.load_sync(...) 取 prompt 字符串。"""
    return type("PM", (), {"load_sync": lambda self, *a, **k: "prompt"})()


@pytest.fixture
def captured(monkeypatch):
    """捕获 run_claude_prompt 的 provider_config 实参 + 屏蔽 execute 成功路径的副作用。"""
    box = {}

    async def fake_run(prompt, repo_path, **kw):
        box["provider_config"] = kw.get("provider_config")
        return ClaudeRunResult(success=True, structured_output=None)

    monkeypatch.setattr("supernova_core.agents.executor.run_claude_prompt", fake_run)
    # GitManager 全为 @staticmethod async（await 调用）；用 async noop 屏蔽成功路径副作用。
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("supernova_core.agents.executor.GitManager.ensure_repository", _noop)
    monkeypatch.setattr("supernova_core.agents.executor.GitManager.create_checkpoint", _noop)
    monkeypatch.setattr("supernova_core.agents.executor.GitManager.commit", _noop)
    # success 路径 spending-cap 检查（cost=0 不早退，这里直接钉死 False 确保不 rollback）。
    monkeypatch.setattr("supernova_core.agents.executor.is_spending_cap_behavior",
                        lambda *a, **k: False)
    return box


async def test_execute_passes_provider_config(captured, tmp_path):
    """execute 收 provider_config → run_claude_prompt 收到同一 dict。"""
    exe = AgentExecutor(prompt_manager=_stub_prompt_manager())
    pc = {"type": "openai_compatible", "api_key": "sk-stage1", "max_turns": 777}
    await exe.execute(
        agent_name=AgentName.RECON,
        repo_path=str(tmp_path),
        deliverables_path=str(tmp_path / "deliv"),
        provider_config=pc,
        skip_artifact_postprocess=True,
    )
    assert captured["provider_config"] == pc


async def test_execute_provider_config_default_none(captured, tmp_path):
    """不传 provider_config → run_claude_prompt 收 None（CLI 兜底 env，行为不变）。"""
    exe = AgentExecutor(prompt_manager=_stub_prompt_manager())
    await exe.execute(
        agent_name=AgentName.RECON,
        repo_path=str(tmp_path),
        deliverables_path=str(tmp_path / "deliv"),
        skip_artifact_postprocess=True,
    )
    assert captured["provider_config"] is None
