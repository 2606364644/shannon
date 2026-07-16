"""验证 skip_artifact_postprocess=True 时跳过 queue 写回 + deliverable 校验。

用 monkeypatch 把 run_claude_prompt / GitManager / validate_deliverable 打桩，
避免真跑 LLM。重点断言：skip=True 时 structured_output 不被写回 queue 文件，
且 validate_deliverable 不被调用。
"""
import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_skip_postprocess_avoids_queue_write(tmp_path, monkeypatch):
    from shannon_core.agents import executor as exec_mod

    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    # 桩 run_claude_prompt：返回带 structured_output 的成功结果
    class _R:
        success = True
        turns = 1
        cost = 0.01
        cost_currency = "USD"
        text = ""
        error = None
        retryable = True
        model = "stub"
        stop_reason = "end_turn"

        class tokens:
            input_tokens = 1
            output_tokens = 1
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        structured_output = {"verdicts": [{"vulnerability_id": "X-VULN-1",
                                           "status": "false_positive",
                                           "reason": "r", "evidence": "e"}]}

    async def fake_run(**kw):
        return _R()

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    monkeypatch.setattr(exec_mod.GitManager, "ensure_repository",
                        classmethod(lambda cls, p: asyncio.sleep(0)))
    monkeypatch.setattr(exec_mod.GitManager, "create_checkpoint",
                        lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(exec_mod.GitManager, "commit",
                        lambda *a, **k: asyncio.sleep(0))
    # validate_deliverable 也必须不被调用：换成会炸的哨兵
    async def boom_validate(*a, **k):
        raise AssertionError("validate_deliverable must NOT be called when skip=True")
    monkeypatch.setattr(exec_mod, "validate_deliverable", boom_validate)

    from shannon_core.prompts.manager import PromptManager
    pm = PromptManager.__new__(PromptManager)
    pm.prompts_dir = tmp_path
    monkeypatch.setattr(pm, "load_sync", lambda *a, **k: "PROMPT")

    ax = exec_mod.AgentExecutor(pm)

    # skip=True：不应写 queue、不应校验 deliverable
    metrics = _run(ax.execute(
        agent_name=exec_mod.AgentName.INJECTION_EXPLOIT,
        repo_path=str(deliverables), deliverables_path=str(deliverables),
        structured_output_schema={"type": "object"},
        skip_artifact_postprocess=True,
    ))
    # verdicts 不应落盘成 queue 文件
    assert not (deliverables / "injection_exploitation_queue.json").exists()
    assert metrics.structured_output == _R.structured_output
