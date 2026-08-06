"""executor 对 provider 合法 ErrorCode 的透传守卫。

验证：result.error_code 是 ErrorCode enum 时透传到 PentestError.error_code；
是非 enum 字符串时保持 AGENT_EXECUTION_FAILED（避免破坏 RateLimit/Timeout 分类）。
"""
import asyncio

import pytest

from supernova_core.agents import executor as exec_mod
from supernova_core.models.errors import ErrorCode, PentestError


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
    from supernova_core.prompts.manager import PromptManager
    pm = PromptManager.__new__(PromptManager)
    pm.prompts_dir = tmp_path
    monkeypatch.setattr(pm, "load_sync", lambda *a, **k: "PROMPT")
    return deliverables, exec_mod.AgentExecutor(pm)


def _stub_result(*, error_code, cost=0.0, cost_currency="USD", tokens=None,
                 model="stub", turns=1):
    class _R:
        text = ""
        error = "structured output parse failed"
        retryable = True
        stop_reason = "end_turn"
    r = _R()
    r.success = False
    r.turns = turns
    r.cost = cost
    r.cost_currency = cost_currency
    r.tokens = tokens
    r.model = model
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


def test_executor_keeps_agent_execution_failed_for_none_code(tmp_path, monkeypatch):
    """result.error_code=None（provider 未设）→ AGENT_EXECUTION_FAILED（None 不是 ErrorCode 实例）。"""
    deliverables, ax = _patch_runtime(monkeypatch, tmp_path)

    async def fake_run(**kw):
        await asyncio.sleep(0)
        return _stub_result(error_code=None)

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    with pytest.raises(PentestError) as exc:
        _run(ax.execute(
            agent_name=exec_mod.AgentName.INJECTION_VULN,
            repo_path=str(deliverables), deliverables_path=str(deliverables),
            skip_artifact_postprocess=True,
        ))
    assert exc.value.error_code == ErrorCode.AGENT_EXECUTION_FAILED


def test_executor_pentest_error_carries_cost_context_on_failure(tmp_path, monkeypatch):
    """L2：agent 失败（not result.success）raise PentestError 时，result.cost/tokens 经
    context 携带，供 activities 失败路径记进 metrics（修 error path cost 归 0）。"""
    from supernova_core.agents.runner import TokenUsage
    deliverables, ax = _patch_runtime(monkeypatch, tmp_path)
    tokens = TokenUsage(input_tokens=1234, output_tokens=567)

    async def fake_run(**kw):
        await asyncio.sleep(0)
        return _stub_result(
            error_code=ErrorCode.AGENT_EXECUTION_FAILED,
            cost=0.1234, cost_currency="CNY", tokens=tokens,
            model="glm-5.2", turns=3,
        )

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    with pytest.raises(PentestError) as exc:
        _run(ax.execute(
            agent_name=exec_mod.AgentName.INJECTION_VULN,
            repo_path=str(deliverables), deliverables_path=str(deliverables),
            skip_artifact_postprocess=True,
        ))
    ctx = exc.value.context
    assert ctx["cost_usd"] == 0.1234
    assert ctx["cost_currency"] == "CNY"
    assert ctx["model"] == "glm-5.2"
    assert ctx["num_turns"] == 3
    assert ctx["input_tokens"] == 1234
    assert ctx["output_tokens"] == 567
