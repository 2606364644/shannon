"""executor 对 provider 合法 ErrorCode 的透传守卫。

验证：result.error_code 是 ErrorCode enum 时透传到 PentestError.error_code；
是非 enum 字符串时保持 AGENT_EXECUTION_FAILED（避免破坏 RateLimit/Timeout 分类）。
"""
import asyncio

import pytest

from shannon_core.agents import executor as exec_mod
from shannon_core.models.errors import ErrorCode, PentestError


def _run(coro):
    return asyncio.run(coro)


def _patch_runtime(monkeypatch, tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    monkeypatch.setattr(exec_mod.GitManager, "ensure_repository",
                        classmethod(lambda cls, p: asyncio.sleep(0)))
    monkeypatch.setattr(exec_mod.GitManager, "create_checkpoint",
                        lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(exec_mod.GitManager, "rollback",
                        lambda *a, **k: asyncio.sleep(0))
    from shannon_core.prompts.manager import PromptManager
    pm = PromptManager.__new__(PromptManager)
    pm.prompts_dir = tmp_path
    monkeypatch.setattr(pm, "load_sync", lambda *a, **k: "PROMPT")
    return deliverables, exec_mod.AgentExecutor(pm)


def _stub_result(*, error_code):
    class _R:
        success = False
        turns = 1
        cost = 0.0
        text = ""
        error = "structured output parse failed"
        retryable = True
        model = "stub"
        stop_reason = "end_turn"
    r = _R()
    r.error_code = error_code
    return r


def test_executor_passes_output_validation_failed(tmp_path, monkeypatch):
    deliverables, ax = _patch_runtime(monkeypatch, tmp_path)

    async def fake_run(**kw):
        await asyncio.sleep(0)
        return _stub_result(error_code=ErrorCode.OUTPUT_VALIDATION_FAILED)

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    with pytest.raises(PentestError) as exc:
        _run(ax.execute(
            agent_name=exec_mod.AgentName.INJECTION_VULN,
            repo_path=str(deliverables), deliverables_path=str(deliverables),
            skip_artifact_postprocess=True,
        ))
    assert exc.value.error_code == ErrorCode.OUTPUT_VALIDATION_FAILED


def test_executor_keeps_agent_execution_failed_for_string_code(tmp_path, monkeypatch):
    """provider 的字符串 error_code（Temporal error type，非 enum）不透传。"""
    deliverables, ax = _patch_runtime(monkeypatch, tmp_path)

    async def fake_run(**kw):
        await asyncio.sleep(0)
        return _stub_result(error_code="RateLimitError")

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    with pytest.raises(PentestError) as exc:
        _run(ax.execute(
            agent_name=exec_mod.AgentName.INJECTION_VULN,
            repo_path=str(deliverables), deliverables_path=str(deliverables),
            skip_artifact_postprocess=True,
        ))
    assert exc.value.error_code == ErrorCode.AGENT_EXECUTION_FAILED
