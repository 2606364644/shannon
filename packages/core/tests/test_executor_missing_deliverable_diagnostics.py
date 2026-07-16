"""诊断不改行为:agent success 但 deliverable 缺失时,executor 在 re-raise 前补充诊断。

根因(systematic-debugging 2026-07-17 定位):GLM 长任务 + 子代理委派后失忆,
agent end_turn(success=True)但没执行 Write 步骤(pre-recon prompt 的 Phase 序,
Phase 3 才写文件)。validate_deliverable 只检查文件存在性;此处补全诊断
(final text / 目录实际产物 / turns / stop_reason),经 session.log_error →
workflow.log [ERROR] 行 + activity_failures.log 可见,便于定位。

不改错误码 / retryable(行为不变,仍走 OUTPUT_VALIDATION retry cap=3)。
所有 .md 产物 agent 共性(pre-recon 最易触发);vuln 的 queue 缺失同样适用。
"""
import asyncio

import pytest


def _run(coro):
    return asyncio.run(coro)


def _stub_result(*, text="", turns=0, stop_reason="end_turn", structured_output=None):
    """构造一个 success=True 的 ClaudeRunResult 替身,模拟 agent 正常结束。"""

    class _tokens:
        input_tokens = 0
        output_tokens = 0
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0

    class _R:
        success = True
        cost = 0.0
        cost_currency = "USD"
        error = None
        retryable = True
        model = "stub"
        tokens = _tokens

    _R.text = text
    _R.turns = turns
    _R.stop_reason = stop_reason
    _R.structured_output = structured_output
    return _R()


def _make_executor(tmp_path, monkeypatch, run_result):
    from shannon_core.agents import executor as exec_mod

    async def fake_run(**kw):
        return run_result

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    monkeypatch.setattr(exec_mod.GitManager, "ensure_repository",
                        classmethod(lambda cls, p: asyncio.sleep(0)))
    monkeypatch.setattr(exec_mod.GitManager, "create_checkpoint",
                        lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(exec_mod.GitManager, "commit",
                        lambda *a, **k: asyncio.sleep(0))

    from shannon_core.prompts.manager import PromptManager
    pm = PromptManager.__new__(PromptManager)
    pm.prompts_dir = tmp_path
    monkeypatch.setattr(pm, "load_sync", lambda *a, **k: "PROMPT")
    return exec_mod.AgentExecutor(pm)


def test_missing_deliverable_enriches_diagnostics_without_changing_behavior(
    tmp_path, monkeypatch
):
    """agent success 但 pre_recon_deliverable.md 缺失 → 抛 OUTPUT_VALIDATION_FAILED
    且 error 携带诊断(final_text / turns / stop_reason / dir_listing / has_structured_output);
    错误码与原 retryable 不变(行为不变)。"""
    from shannon_core.models.errors import PentestError, ErrorCode
    from shannon_core.models.agents import AgentName

    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # 不写 pre_recon_deliverable.md —— 模拟 GLM 失忆没 Write(对齐 2026-07-16 NodeGoat 现场)

    run_result = _stub_result(
        text="入口点映射代理已完成,仍在等待架构扫描和安全模式猎手两个代理完成,之后才能启动 Phase 2。",
        turns=147, stop_reason="end_turn", structured_output=None,
    )
    ax = _make_executor(tmp_path, monkeypatch, run_result)

    with pytest.raises(PentestError) as exc_info:
        _run(ax.execute(
            agent_name=AgentName.PRE_RECON,
            repo_path=str(deliverables),
            deliverables_path=str(deliverables),
        ))

    err = exc_info.value
    # 行为不变:仍是 OUTPUT_VALIDATION_FAILED(classify → retryable=True,retry cap=3 不变)
    assert err.error_code == ErrorCode.OUTPUT_VALIDATION_FAILED

    ctx = err.context
    assert ctx["expected_deliverable"] == "pre_recon_deliverable.md"
    assert ctx["final_turns"] == 147
    assert ctx["stop_reason"] == "end_turn"
    assert ctx["has_structured_output"] is False
    # 目录里 agent 实际写了什么(此处空 → 一份文件都没产出)
    assert ctx["deliverables_listing"] == []
    assert "仍在等待" in ctx["final_text_preview"]
    assert ctx["final_text_len"] == len(run_result.text.strip())
    # 诊断摘要拼进 message,经 log_error 的 str(error) 渲染可见
    assert "diagnostics" in err.message.lower()
    assert "147" in err.message


def test_missing_deliverable_listing_captures_other_files(tmp_path, monkeypatch):
    """agent 写了别的文件但漏了目标 deliverable → listing 反映实际产物,
    便于判断是「完全没写」还是「写错文件」。"""
    from shannon_core.models.errors import PentestError
    from shannon_core.models.agents import AgentName

    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # agent 写了 scratchpad 但漏了 pre_recon_deliverable.md
    (deliverables / "scratchpad.md").write_text("notes")

    ax = _make_executor(tmp_path, monkeypatch, _stub_result(text="x", turns=10))

    with pytest.raises(PentestError) as exc_info:
        _run(ax.execute(
            agent_name=AgentName.PRE_RECON,
            repo_path=str(deliverables),
            deliverables_path=str(deliverables),
        ))

    assert exc_info.value.context["deliverables_listing"] == ["scratchpad.md"]


def test_deliverable_present_does_not_inject_diagnostics(tmp_path, monkeypatch):
    """回归保护:deliverable 存在 → 正常返回,不抛错、无诊断注入。"""
    from shannon_core.models.agents import AgentName

    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "pre_recon_deliverable.md").write_text("# Analysis")

    ax = _make_executor(tmp_path, monkeypatch, _stub_result(text="done", turns=5))

    metrics = _run(ax.execute(
        agent_name=AgentName.PRE_RECON,
        repo_path=str(deliverables),
        deliverables_path=str(deliverables),
    ))
    assert metrics.num_turns == 5
