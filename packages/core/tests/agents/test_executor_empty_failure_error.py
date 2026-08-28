"""executor 失败分支空 error 防漏（2026-08-28 NodeGoat-20260828-054537 后续）。

现场：run_claude_prompt 返回 success=False + error 空 → executor
`raise PentestError(result.error or f"Agent ... execution failed")` 落 fallback
且不留任何日志——「error 在到达 executor 前已丢失」这一事实本身无证据。
provider 侧已修空消息异常兜底（test_providers_exception_path_observability.py），
但 executor 是最后一道关口：上游任何新路径再丢 error 时，这里必须留痕。

本文件锁定：失败且 result.error 为空时，raise 前必须落 warning（含定位上下文），
fallback 消息行为保持不变。
"""
import logging

import pytest

from supernova_core.agents.executor import AgentExecutor
from supernova_core.agents.runner import ClaudeRunResult
from supernova_core.models.agents import AgentName
from supernova_core.models.errors import PentestError

LOGGER_NAME = "supernova_core.agents.executor"


def _stub_prompt_manager():
    return type("PM", (), {"load_sync": lambda self, *a, **k: "prompt"})()


@pytest.fixture
def failure_shell(monkeypatch):
    """屏蔽失败路径副作用（GitManager / spending-cap），run_claude_prompt 返回现场形态。"""
    async def failing_run(prompt, repo_path, **kw):
        return ClaudeRunResult(success=False, error=None, turns=153, cost=0.4368)

    monkeypatch.setattr("supernova_core.agents.executor.run_claude_prompt", failing_run)

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr("supernova_core.agents.executor.GitManager.ensure_repository", _noop)
    monkeypatch.setattr("supernova_core.agents.executor.GitManager.create_checkpoint", _noop)
    monkeypatch.setattr("supernova_core.agents.executor.GitManager.rollback", _noop)
    return None


async def test_execute_warns_on_empty_failure_error(failure_shell, tmp_path, caplog):
    """失败 + result.error 空：fallback PentestError 照抛，但空 error 必须留 warning 痕。"""
    exe = AgentExecutor(prompt_manager=_stub_prompt_manager())
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        with pytest.raises(PentestError) as ei:
            await exe.execute(
                agent_name=AgentName.RECON,
                repo_path=str(tmp_path),
                deliverables_path=str(tmp_path / "deliv"),
                skip_artifact_postprocess=True,
            )
    # fallback 消息行为保持（本修复只加可观测性，不改语义）
    assert "execution failed" in str(ei.value)
    # 空 error 事实必须落盘（上游丢失的实证锚点）
    assert "empty result.error" in caplog.text


async def test_execute_no_empty_error_warning_when_error_present(failure_shell, tmp_path, caplog, monkeypatch):
    """失败但 error 非空：不打空 error warning（防噪音，错误已可定位）。"""
    async def failing_run(prompt, repo_path, **kw):
        return ClaudeRunResult(success=False, error="SDK result failure: subtype=error_max_turns")

    monkeypatch.setattr("supernova_core.agents.executor.run_claude_prompt", failing_run)
    exe = AgentExecutor(prompt_manager=_stub_prompt_manager())
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        with pytest.raises(PentestError) as ei:
            await exe.execute(
                agent_name=AgentName.RECON,
                repo_path=str(tmp_path),
                deliverables_path=str(tmp_path / "deliv"),
                skip_artifact_postprocess=True,
            )
    assert "SDK result failure" in str(ei.value)  # 真实 error 透传
    assert "empty result.error" not in caplog.text
