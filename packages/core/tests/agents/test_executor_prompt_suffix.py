"""AgentExecutor.execute 的 prompt_suffix 通道（MR 增量引导段，spec 2026-09-03 §5.2）。

渲染后追加：模板产物 + suffix 直达 run_claude_prompt.prompt。
None（默认）= 零行为变化（全量扫描）。
"""

import pytest

from supernova_core.agents.executor import AgentExecutor
from supernova_core.agents.runner import ClaudeRunResult
from supernova_core.models.agents import AgentName


def _stub_prompt_manager():
    return type("PM", (), {"load_sync": lambda self, *a, **k: "BASE-PROMPT"})()


@pytest.fixture
def captured(monkeypatch):
    box = {}

    async def fake_run(prompt, repo_path, **kw):
        box["prompt"] = prompt
        return ClaudeRunResult(success=True, structured_output=None)

    monkeypatch.setattr("supernova_core.agents.executor.run_claude_prompt", fake_run)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("supernova_core.agents.executor.GitManager.ensure_repository", _noop)
    monkeypatch.setattr("supernova_core.agents.executor.GitManager.create_checkpoint", _noop)
    monkeypatch.setattr("supernova_core.agents.executor.GitManager.commit", _noop)
    monkeypatch.setattr("supernova_core.agents.executor.is_spending_cap_behavior",
                        lambda *a, **k: False)
    return box


async def test_execute_appends_prompt_suffix_after_template(captured, tmp_path):
    exe = AgentExecutor(prompt_manager=_stub_prompt_manager())
    await exe.execute(
        agent_name=AgentName.RECON,
        repo_path=str(tmp_path),
        deliverables_path=str(tmp_path / "deliv"),
        prompt_suffix="\n\n--- 增量扫描上下文 ---\nbase abc..head def",
        skip_artifact_postprocess=True,
    )
    assert captured["prompt"].startswith("BASE-PROMPT")
    assert captured["prompt"].endswith("base abc..head def")


async def test_execute_without_suffix_leaves_prompt_untouched(captured, tmp_path):
    exe = AgentExecutor(prompt_manager=_stub_prompt_manager())
    await exe.execute(
        agent_name=AgentName.RECON,
        repo_path=str(tmp_path),
        deliverables_path=str(tmp_path / "deliv"),
        skip_artifact_postprocess=True,
    )
    assert captured["prompt"] == "BASE-PROMPT"
